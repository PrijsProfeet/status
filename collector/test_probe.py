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
    _update_freshness,
    _update_sla,
    fetch_freshness,
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


class TestFreshnessPerHost(unittest.TestCase):
    """Freshness per chain (#1398): read per host, kept on a failed fetch."""

    def _answer(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        original = probe._fetch
        probe._fetch = lambda url: (status, {}, body, None)
        self.addCleanup(setattr, probe, "_fetch", original)

    def test_a_summary_is_kept(self):
        self._answer(200, {"norm": 0.9, "retailers": [{"retailer": "jumbo"}]})
        summary = fetch_freshness("https://www.prijsprofeet.nl")
        self.assertEqual(summary["retailers"][0]["retailer"], "jumbo")

    def test_an_error_or_garbage_is_no_answer(self):
        self._answer(503, {"detail": "down"})
        self.assertIsNone(fetch_freshness("https://www.prijsprofeet.nl"))
        self._answer(200, b"<html>")
        self.assertIsNone(fetch_freshness("https://www.prijsprofeet.nl"))
        self._answer(200, {"norm": 0.9})
        self.assertIsNone(fetch_freshness("https://www.prijsprofeet.nl"))

    def test_each_host_under_its_own_key_and_a_failure_keeps_the_last(self):
        data = _fresh_data()
        _update_freshness(data, "www.prijsprofeet.nl", {"retailers": ["nl"]})
        _update_freshness(data, "www.prijsprofeet.be", {"retailers": ["be"]})
        _update_freshness(data, "www.prijsprofeet.nl", None)
        self.assertEqual(
            data["freshness"],
            {
                "www.prijsprofeet.nl": {"retailers": ["nl"]},
                "www.prijsprofeet.be": {"retailers": ["be"]},
            },
        )


class TestDutchDetails(unittest.TestCase):
    def test_known_urllib_errors_are_translated(self):
        self.assertEqual(
            probe.dutch_detail("unreachable: The read operation timed out"),
            "onbereikbaar: time-out",
        )
        self.assertEqual(
            probe.dutch_detail("unreachable: <urlopen error [Errno 104] Connection reset by peer>"),
            "onbereikbaar: verbinding verbroken",
        )

    def test_an_unknown_reason_is_kept_verbatim(self):
        self.assertEqual(probe.dutch_detail("unreachable: weird"), "onbereikbaar: weird")
        self.assertEqual(probe.dutch_detail("HTTP 525"), "HTTP 525")

    def test_translating_twice_changes_nothing(self):
        once = probe.dutch_detail("unreachable: timed out")
        self.assertEqual(probe.dutch_detail(once), once)

    def test_stored_english_rows_are_translated_on_load(self):
        data = _fresh_data()
        _update_service(data, "api", "API", "url", CheckResult(False, "unreachable: timed out"))
        data["services"]["api"]["detail"] = "unreachable: timed out"
        data["incidents"] = [{"detail": "unreachable: timed out"}]
        probe._translate_details(data)
        self.assertEqual(data["services"]["api"]["detail"], "onbereikbaar: time-out")
        self.assertEqual(
            data["services"]["api"]["history"][0]["first_failure"]["detail"],
            "onbereikbaar: time-out",
        )
        self.assertEqual(data["incidents"][0]["detail"], "onbereikbaar: time-out")


class TestNotices(unittest.TestCase):
    def _load(self, raw):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "notices.json"
            path.write_text(raw if isinstance(raw, str) else json.dumps(raw))
            return probe.load_notices(path)

    def _notice(self, **over):
        n = {
            "id": "onderhoud-1",
            "title": "Onderhoud",
            "body": "De API is kort onbereikbaar.",
            "published": "2026-10-09T09:00:00+02:00",
        }
        n.update(over)
        return n

    def test_the_committed_notices_file_is_valid(self):
        # The workflow runs these tests before the probe, so a broken edit to
        # notices.json turns the run red before it publishes anything.
        probe.load_notices()

    def test_a_valid_notice_loads(self):
        [n] = self._load([self._notice(until="2026-10-09T12:00:00+02:00")])
        self.assertEqual(n["id"], "onderhoud-1")
        self.assertEqual(n["until"], "2026-10-09T12:00:00+02:00")

    def test_a_missing_file_is_no_notices(self):
        from pathlib import Path

        self.assertEqual(probe.load_notices(Path("/nonexistent/notices.json")), [])

    def test_broken_input_is_refused(self):
        cases = [
            "{not json",
            {"id": "x"},
            [self._notice(id="Met Spatie")],
            [self._notice(), self._notice()],
            [self._notice(title=" ")],
            [self._notice(published="2026-10-09T09:00:00")],
            [self._notice(published="morgen")],
            [self._notice(until="2026-10-09T08:00:00+02:00")],
            [self._notice(extra="x")],
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(probe.NoticeError):
                self._load(raw)


class TestFeed(unittest.TestCase):
    INCIDENT = {
        "service": "api_be",
        "name": "API (België)",
        "started_at": "2026-10-06T22:35:41+00:00",
        "resolved_at": "2026-10-06T22:40:38+00:00",
        "detail": "onbereikbaar: time-out",
    }
    NOTICE = {
        "id": "onderhoud-1",
        "title": "Onderhoud",
        "body": "Kort onbereikbaar.",
        "published": "2026-10-09T09:00:00+02:00",
        "until": None,
    }

    def _parse(self, xml):
        import xml.etree.ElementTree as ET

        return ET.fromstring(xml)

    def test_a_resolution_is_its_own_entry(self):
        root = self._parse(probe.build_feed([self.INCIDENT], []))
        titles = [e.findtext(f"{{{probe.ATOM_NS}}}title") for e in root.iter(f"{{{probe.ATOM_NS}}}entry")]
        self.assertEqual(titles, ["Opgelost: API (België)", "Storing: API (België)"])

    def test_an_open_incident_has_only_its_start(self):
        open_incident = dict(self.INCIDENT, resolved_at=None)
        root = self._parse(probe.build_feed([open_incident], []))
        self.assertEqual(len(list(root.iter(f"{{{probe.ATOM_NS}}}entry"))), 1)

    def test_times_are_amsterdam_and_the_text_is_dutch(self):
        xml = probe.build_feed([self.INCIDENT], [])
        self.assertIn("Begonnen 7 oktober 00:35", xml)
        self.assertIn("na 5 min", xml)

    def test_the_same_input_gives_the_same_bytes(self):
        a = probe.build_feed([self.INCIDENT], [self.NOTICE])
        b = probe.build_feed([dict(self.INCIDENT)], [dict(self.NOTICE)])
        self.assertEqual(a, b)

    def test_updated_follows_the_newest_entry_not_the_clock(self):
        root = self._parse(probe.build_feed([self.INCIDENT], [self.NOTICE]))
        self.assertEqual(root.findtext(f"{{{probe.ATOM_NS}}}updated"), self.NOTICE["published"])
        empty = self._parse(probe.build_feed([], []))
        self.assertEqual(empty.findtext(f"{{{probe.ATOM_NS}}}updated"), probe.FEED_EPOCH)

    def test_markup_in_a_notice_is_escaped(self):
        notice = dict(self.NOTICE, body="<script>x</script> & meer")
        xml = probe.build_feed([], [notice])
        self.assertNotIn("<script>", xml)
        self._parse(xml)


class TestLocalDays(unittest.TestCase):
    def test_a_summer_night_after_midnight_is_the_next_day(self):
        # 22:30 UTC on 6 Oct is 00:30 on 7 Oct in NL/BE (CEST, +02:00).
        self.assertEqual(
            probe._local_day(datetime(2026, 10, 6, 22, 30, tzinfo=timezone.utc)), "2026-10-07"
        )
        self.assertEqual(
            probe._local_day(datetime(2026, 10, 6, 21, 30, tzinfo=timezone.utc)), "2026-10-06"
        )

    def test_a_winter_night_shifts_one_hour_less(self):
        # CET, +01:00: 22:30 UTC is still 23:30 the same day.
        self.assertEqual(
            probe._local_day(datetime(2026, 12, 1, 22, 30, tzinfo=timezone.utc)), "2026-12-01"
        )
        self.assertEqual(
            probe._local_day(datetime(2026, 12, 1, 23, 30, tzinfo=timezone.utc)), "2026-12-02"
        )

    def test_a_check_is_counted_on_todays_local_day(self):
        data = _fresh_data()
        _update_service(data, "api", "API", "url", CheckResult(True, "200 OK"))
        today = probe._local_day(datetime.now(timezone.utc))
        self.assertEqual([d["date"] for d in data["services"]["api"]["history"]], [today])
