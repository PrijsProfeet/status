#!/usr/bin/env python3
"""Unit tests for the incident-tracking logic in probe.py (#954).

Stdlib-only, run with `python3 -m unittest collector/test_probe.py` (or
`python3 collector/test_probe.py`) — no pytest dependency, matching probe.py's
own zero-dependency policy.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone

from probe import (
    INCIDENT_HISTORY_DAYS,
    INCIDENT_THRESHOLD,
    SITES,
    STALL_THRESHOLD_MINUTES,
    CheckResult,
    _prune_incidents,
    _stall_minutes,
    _update_service,
    _update_sla,
    fetch_sla_summary,
)
import probe


def _fresh_data():
    return {"generated_at": None, "services": {}, "sla": {}, "incidents": []}


class TestIncidentThreshold(unittest.TestCase):
    def test_a_single_blip_opens_no_incident(self):
        data = _fresh_data()
        _update_service(data, "api", "API", "url", CheckResult(False, "HTTP 503"))
        self.assertEqual(data["incidents"], [])
        self.assertEqual(data["services"]["api"]["consecutive_failures"], 1)

    def test_reaching_the_threshold_opens_one_incident(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "HTTP 503"))
        self.assertEqual(len(data["incidents"]), 1)
        incident = data["incidents"][0]
        self.assertEqual(incident["service"], "api")
        self.assertIsNone(incident["resolved_at"])

    def test_recovery_closes_the_incident(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "HTTP 503"))
        _update_service(data, "api", "API", "url", CheckResult(True, "200 OK"))

        self.assertEqual(len(data["incidents"]), 1)
        self.assertIsNotNone(data["incidents"][0]["resolved_at"])
        self.assertEqual(data["services"]["api"]["consecutive_failures"], 0)

    def test_a_blip_below_threshold_never_becomes_an_incident_even_after_recovery(
        self,
    ):
        data = _fresh_data()
        _update_service(data, "api", "API", "url", CheckResult(False, "HTTP 503"))
        _update_service(data, "api", "API", "url", CheckResult(True, "200 OK"))
        self.assertEqual(data["incidents"], [])

    def test_a_longer_outage_stays_one_incident_and_tracks_the_latest_detail(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "HTTP 503"))
        _update_service(
            data, "api", "API", "url", CheckResult(False, "unreachable: timeout")
        )
        self.assertEqual(len(data["incidents"]), 1)
        self.assertEqual(data["incidents"][0]["detail"], "unreachable: timeout")

    def test_two_services_track_independent_streaks(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "down"))
        _update_service(data, "website", "Website", "url", CheckResult(False, "down"))

        self.assertEqual(len(data["incidents"]), 1)
        self.assertEqual(data["incidents"][0]["service"], "api")
        self.assertEqual(data["services"]["website"]["consecutive_failures"], 1)

    def test_a_second_outage_after_recovery_opens_a_second_incident(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "down"))
        _update_service(data, "api", "API", "url", CheckResult(True, "200 OK"))
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "down again"))

        self.assertEqual(len(data["incidents"]), 2)
        self.assertIsNotNone(data["incidents"][0]["resolved_at"])
        self.assertIsNone(data["incidents"][1]["resolved_at"])


class TestPruning(unittest.TestCase):
    def _old_iso(self, days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    def test_an_old_resolved_incident_is_dropped(self):
        data = {
            "incidents": [
                {
                    "service": "api",
                    "started_at": self._old_iso(INCIDENT_HISTORY_DAYS + 10),
                    "resolved_at": self._old_iso(INCIDENT_HISTORY_DAYS + 9),
                }
            ]
        }
        _prune_incidents(data)
        self.assertEqual(data["incidents"], [])

    def test_an_old_but_still_open_incident_is_never_dropped(self):
        data = {
            "incidents": [
                {
                    "service": "api",
                    "started_at": self._old_iso(INCIDENT_HISTORY_DAYS + 10),
                    "resolved_at": None,
                }
            ]
        }
        _prune_incidents(data)
        self.assertEqual(len(data["incidents"]), 1)

    def test_incidents_sort_newest_first(self):
        data = {
            "incidents": [
                {"service": "api", "started_at": self._old_iso(5), "resolved_at": None},
                {
                    "service": "api",
                    "started_at": self._old_iso(1),
                    "resolved_at": None,
                },
            ]
        }
        _prune_incidents(data)
        self.assertEqual(
            [i["started_at"] for i in data["incidents"]],
            sorted((i["started_at"] for i in data["incidents"]), reverse=True),
        )


class TestStallDetection(unittest.TestCase):
    """#973: a GitHub Actions job stuck in `queued` blocked every dispatch
    behind it for 90+ minutes with nothing failing loudly. probe.py now
    detects that its own previous write is too old and fails the run (after
    still writing fresh data) so GitHub notifies watchers of the repo."""

    def test_no_previous_run_is_not_a_stall(self):
        now = datetime.now(timezone.utc)
        self.assertIsNone(_stall_minutes(None, now))

    def test_a_recent_previous_run_is_not_a_stall(self):
        now = datetime.now(timezone.utc)
        previous = (now - timedelta(minutes=5)).isoformat()
        self.assertLess(_stall_minutes(previous, now), STALL_THRESHOLD_MINUTES)

    def test_a_gap_past_the_threshold_is_a_stall(self):
        now = datetime.now(timezone.utc)
        previous = (now - timedelta(minutes=STALL_THRESHOLD_MINUTES + 1)).isoformat()
        self.assertGreater(_stall_minutes(previous, now), STALL_THRESHOLD_MINUTES)


class TestBothStorefronts(unittest.TestCase):
    def test_nl_keeps_its_unsuffixed_keys_so_its_history_carries_over(self):
        self.assertEqual(SITES[0][1], "https://www.prijsprofeet.nl")
        self.assertEqual(SITES[0][2], "")

    def test_every_site_gets_its_own_service_keys(self):
        suffixes = [suffix for _group, _url, suffix in SITES]
        self.assertEqual(len(suffixes), len(set(suffixes)))
        self.assertIn("https://www.prijsprofeet.be", [url for _g, url, _s in SITES])


    def test_an_incident_names_the_country_it_happened_in(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(
                data, "api_be", "API", "url", CheckResult(False, "HTTP 525"), "België"
            )
        self.assertEqual(data["incidents"][0]["name"], "API (België)")
        self.assertEqual(data["services"]["api_be"]["group"], "België")

    def test_a_be_outage_leaves_the_nl_rows_alone(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(
                data, "api_be", "API", "url", CheckResult(False, "down"), "België"
            )
            _update_service(
                data, "api", "API", "url", CheckResult(True, "200 OK"), "Nederland"
            )
        self.assertEqual([i["service"] for i in data["incidents"]], ["api_be"])
        self.assertEqual(data["services"]["api"]["status"], "up")


if __name__ == "__main__":
    unittest.main()


class TestSlaPerHost(unittest.TestCase):
    """The Business SLA is owed per host (#1242): each host's track record is
    read on that host and stored under it, never borrowed from another."""

    def _answer(self, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        original = probe._fetch
        probe._fetch = lambda url: (200, {}, body, None)
        self.addCleanup(setattr, probe, "_fetch", original)

    def test_an_answer_for_the_asked_host_is_kept(self):
        self._answer({"host": "www.prijsprofeet.be", "months": []})
        summary = fetch_sla_summary("https://www.prijsprofeet.be")
        self.assertEqual(summary["host"], "www.prijsprofeet.be")

    def test_an_answer_for_another_host_is_refused(self):
        # Before #1242 .be answered with the .nl figures, without a host field.
        self._answer({"target_pct": 99.5, "months": []})
        self.assertIsNone(fetch_sla_summary("https://www.prijsprofeet.be"))
        self._answer({"host": "www.prijsprofeet.nl", "months": []})
        self.assertIsNone(fetch_sla_summary("https://www.prijsprofeet.be"))

    def test_garbage_is_no_answer(self):
        self._answer(b"<html>")
        self.assertIsNone(fetch_sla_summary("https://www.prijsprofeet.nl"))

    def test_each_host_is_stored_under_its_own_key(self):
        data = _fresh_data()
        _update_sla(data, "www.prijsprofeet.nl", {"host": "www.prijsprofeet.nl"})
        _update_sla(data, "www.prijsprofeet.be", {"host": "www.prijsprofeet.be"})
        self.assertEqual(set(data["sla"]), {"www.prijsprofeet.nl", "www.prijsprofeet.be"})

    def test_a_failed_fetch_keeps_the_last_record(self):
        data = _fresh_data()
        _update_sla(data, "www.prijsprofeet.nl", {"host": "www.prijsprofeet.nl"})
        _update_sla(data, "www.prijsprofeet.nl", None)
        self.assertEqual(data["sla"]["www.prijsprofeet.nl"], {"host": "www.prijsprofeet.nl"})

    def test_the_old_single_summary_shape_is_dropped(self):
        data = _fresh_data()
        data["sla"] = {"target_pct": 99.5, "months": [{"month": "2026-08"}]}
        _update_sla(data, "www.prijsprofeet.nl", None)
        self.assertEqual(data["sla"], {})
        data["sla"] = None
        _update_sla(data, "www.prijsprofeet.nl", None)
        self.assertEqual(data["sla"], {})
