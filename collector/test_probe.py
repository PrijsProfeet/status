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
    _update_alert_incidents,
    _feed_entries,
    fetch_alerts,
    fetch_freshness,
    fetch_sla_summary,
)
import probe


def _fresh_data():
    return {"generated_at": None, "services": {}, "sla": {}, "incidents": []}


class TestIncidentThreshold(unittest.TestCase):
    def test_a_single_blip_opens_no_incident(self):
        data = _fresh_data()
        _update_service(data, "api", "API", "url", CheckResult(False, "http_503"))
        self.assertEqual(data["incidents"], [])
        self.assertEqual(data["services"]["api"]["consecutive_failures"], 1)

    def test_reaching_the_threshold_opens_one_incident(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "http_503"))
        self.assertEqual(len(data["incidents"]), 1)
        incident = data["incidents"][0]
        self.assertEqual(incident["service"], "api")
        self.assertIsNone(incident["resolved_at"])

    def test_recovery_closes_the_incident(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "http_503"))
        _update_service(data, "api", "API", "url", CheckResult(True, "ok"))

        self.assertEqual(len(data["incidents"]), 1)
        self.assertIsNotNone(data["incidents"][0]["resolved_at"])
        self.assertEqual(data["services"]["api"]["consecutive_failures"], 0)

    def test_a_blip_below_threshold_never_becomes_an_incident_even_after_recovery(
        self,
    ):
        data = _fresh_data()
        _update_service(data, "api", "API", "url", CheckResult(False, "http_503"))
        _update_service(data, "api", "API", "url", CheckResult(True, "ok"))
        self.assertEqual(data["incidents"], [])

    def test_a_longer_outage_stays_one_incident_and_tracks_the_latest_detail(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "http_503"))
        _update_service(
            data, "api", "API", "url", CheckResult(False, "timeout")
        )
        self.assertEqual(len(data["incidents"]), 1)
        self.assertEqual(data["incidents"][0]["reason"], "timeout")

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
        _update_service(data, "api", "API", "url", CheckResult(True, "ok"))
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(data, "api", "API", "url", CheckResult(False, "down again"))

        self.assertEqual(len(data["incidents"]), 2)
        self.assertIsNotNone(data["incidents"][0]["resolved_at"])
        self.assertIsNone(data["incidents"][1]["resolved_at"])


class TestIncidentStart(unittest.TestCase):
    def test_the_incident_starts_at_the_first_failed_check(self):
        data = _fresh_data()
        data["services"]["api"] = {}
        probe._update_incidents(data, "api", "API", CheckResult(False, "down"), "t1")
        probe._update_incidents(data, "api", "API", CheckResult(False, "down"), "t2")
        self.assertEqual(data["incidents"][0]["started_at"], "t1")

    def test_a_recovery_forgets_the_first_failure(self):
        data = _fresh_data()
        data["services"]["api"] = {}
        probe._update_incidents(data, "api", "API", CheckResult(False, "down"), "t1")
        probe._update_incidents(data, "api", "API", CheckResult(True, "ok"), "t2")
        probe._update_incidents(data, "api", "API", CheckResult(False, "down"), "t3")
        probe._update_incidents(data, "api", "API", CheckResult(False, "down"), "t4")
        self.assertEqual(data["incidents"][0]["started_at"], "t3")
        probe._update_incidents(data, "api", "API", CheckResult(True, "ok"), "t5")
        self.assertNotIn("failing_since", data["services"]["api"])


class TestRetry(unittest.TestCase):
    def setUp(self):
        self._sleep = probe.time.sleep
        probe.time.sleep = lambda _s: None

    def tearDown(self):
        probe.time.sleep = self._sleep

    def _check(self, *answers):
        calls = []

        def check(url):
            calls.append(url)
            return answers[len(calls) - 1]

        return check, calls

    def test_a_pass_is_not_retried(self):
        check, calls = self._check(CheckResult(True, "ok"))
        self.assertTrue(probe.with_retry(check, "u").ok)
        self.assertEqual(len(calls), 1)

    def test_a_dropped_connection_that_recovers_counts_as_up(self):
        check, calls = self._check(
            CheckResult(False, "timeout"), CheckResult(True, "ok")
        )
        self.assertTrue(probe.with_retry(check, "u").ok)
        self.assertEqual(len(calls), 2)

    def test_a_host_that_fails_twice_counts_as_down_with_the_second_reason(self):
        check, _ = self._check(
            CheckResult(False, "timeout"), CheckResult(False, "http_503")
        )
        result = probe.with_retry(check, "u")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "http_503")

    def test_main_retries_every_check(self):
        import inspect

        src = inspect.getsource(probe.main)
        self.assertNotRegex(src, r"[^_]check_(api|website)\(")
        self.assertIn("with_retry(check_api", src)
        self.assertIn("with_retry(check_website", src)
        self.assertIn("with_retry(check_mcp", src)


class TestFreshnessIncidents(unittest.TestCase):
    HOST = "www.prijsprofeet.nl"

    def _summary(self, day, fresh, retailer="albert_heijn", name="Albert Heijn"):
        return {
            "fresh_by": "07:00",
            "retailers": [{"retailer": retailer, "name": name, "latest": {"day": day, "fresh": fresh}}],
        }

    def _run(self, data, day, fresh, **kw):
        probe._update_freshness_incidents(data, self.HOST, "nl", self._summary(day, fresh, **kw))

    def test_a_stale_chain_opens_an_incident_at_seven_dutch_time(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", False)
        [i] = data["incidents"]
        self.assertEqual(i["service"], "versheid:albert_heijn:www.prijsprofeet.nl")
        self.assertEqual(i["name"], "Albert Heijn (Nederland)")
        self.assertEqual(i["started_at"], "2026-10-12T05:00:00+00:00")
        self.assertIsNone(i["resolved_at"])

    def test_staying_stale_keeps_one_incident_and_its_start(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", False)
        self._run(data, "2026-10-13", False)
        self._run(data, "2026-10-13", False)
        [i] = data["incidents"]
        self.assertEqual(i["started_at"], "2026-10-12T05:00:00+00:00")

    def test_the_first_fresh_morning_closes_it(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", False)
        self._run(data, "2026-10-15", True)
        [i] = data["incidents"]
        self.assertEqual(i["resolved_at"], "2026-10-15T05:00:00+00:00")

    def test_a_fresh_chain_opens_nothing(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", True)
        self.assertEqual(data["incidents"], [])

    def test_a_failed_fetch_leaves_an_open_incident_open(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", False)
        probe._update_freshness_incidents(data, self.HOST, "nl", None)
        self.assertIsNone(data["incidents"][0]["resolved_at"])

    def test_each_host_has_its_own_incident(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", False)
        probe._update_freshness_incidents(
            data, "www.prijsprofeet.be", "be",
            self._summary("2026-10-12", False, retailer="albert_heijn_be"),
        )
        self.assertEqual(
            [i["name"] for i in data["incidents"]],
            ["Albert Heijn (Nederland)", "Albert Heijn (België)"],
        )

    def test_the_feed_says_not_fresh_and_fresh_again_not_storing(self):
        data = _fresh_data()
        self._run(data, "2026-10-12", False)
        self._run(data, "2026-10-15", True)
        root = ET_parse(probe.build_feed(data["incidents"], []))
        titles = [e.findtext(f"{{{probe.ATOM_NS}}}title") for e in root.iter(f"{{{probe.ATOM_NS}}}entry")]
        self.assertEqual(titles, ["Weer vers: Albert Heijn (Nederland)", "Niet vers: Albert Heijn (Nederland)"])
        xml = probe.build_feed(data["incidents"], [])
        self.assertIn("Om 12 oktober 07:00 was de data", xml)
        self.assertIn("na 3 dagen", xml)
        self.assertNotIn("Storing:", xml)

    def test_main_feeds_the_freshness_answer_into_the_incidents(self):
        import inspect

        self.assertIn("_update_freshness_incidents(", inspect.getsource(probe.main))


def ET_parse(xml):
    import xml.etree.ElementTree as ET

    return ET.fromstring(xml)


class TestDuration(unittest.TestCase):
    def test_days(self):
        self.assertEqual(probe._duration("2026-10-12T05:00:00+00:00", "2026-10-13T05:00:00+00:00"), "1 dag")
        self.assertEqual(probe._duration("2026-10-12T05:00:00+00:00", "2026-10-14T08:00:00+00:00"), "2 dagen 3 u")
        self.assertEqual(probe._duration("2026-10-12T05:00:00+00:00", "2026-10-12T08:30:00+00:00"), "3 u 30 min")


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
        suffixes = [suffix for _country, _url, suffix in SITES]
        self.assertEqual(len(suffixes), len(set(suffixes)))
        self.assertIn("https://www.prijsprofeet.be", [url for _g, url, _s in SITES])


    def test_an_incident_names_the_country_it_happened_in(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(
                data, "api_be", "API", "url", CheckResult(False, "http_525"), "be"
            )
        self.assertEqual(data["incidents"][0]["name"], "API (België)")
        self.assertEqual(data["services"]["api_be"]["country"], "be")
        self.assertEqual(data["incidents"][0]["country"], "be")

    def test_a_be_outage_leaves_the_nl_rows_alone(self):
        data = _fresh_data()
        for _ in range(INCIDENT_THRESHOLD):
            _update_service(
                data, "api_be", "API", "url", CheckResult(False, "down"), "be"
            )
            _update_service(
                data, "api", "API", "url", CheckResult(True, "ok"), "nl"
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


class TestAlertIncidents(unittest.TestCase):
    """Public Prometheus alerts (#954): pulled per host, an incident each, and
    closed only by an answer that no longer lists them."""

    NL = "www.prijsprofeet.nl"
    BE = "www.prijsprofeet.be"
    SLOW = {"id": "PublicAPISlow", "title": "De API reageert traag", "since": "2026-10-13T10:00:00+00:00"}

    def _answer(self, status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        original = probe._fetch
        probe._fetch = lambda url: (status, {}, body, None)
        self.addCleanup(setattr, probe, "_fetch", original)

    def test_an_answer_for_the_asked_host_is_kept(self):
        self._answer(200, {"host": self.NL, "incidents": [self.SLOW]})
        self.assertEqual(fetch_alerts("https://" + self.NL), [self.SLOW])
        self._answer(200, {"host": self.NL, "incidents": []})
        self.assertEqual(fetch_alerts("https://" + self.NL), [])

    def test_an_error_another_host_or_garbage_is_no_answer(self):
        # A 503 (Prometheus down) must never read as "nothing is wrong".
        for status, payload in [
            (503, {"detail": "weg"}),
            (404, {"detail": "Not Found"}),
            (200, b"<html>"),
            (200, {"host": self.NL, "incidents": [self.SLOW]}),
            (200, {"host": self.BE}),
            (200, {"host": self.BE, "incidents": [{"id": "X", "title": "Y", "since": "gisteren"}]}),
        ]:
            self._answer(status, payload)
            self.assertIsNone(fetch_alerts("https://" + self.BE), payload)

    def test_an_alert_opens_one_incident_from_its_own_start(self):
        data = _fresh_data()
        for _ in range(3):
            _update_alert_incidents(data, self.NL, "nl", [self.SLOW], "2026-10-13T10:20:00+00:00")
        (incident,) = data["incidents"]
        self.assertEqual(incident["name"], "De API reageert traag (Nederland)")
        self.assertEqual(incident["started_at"], self.SLOW["since"])
        self.assertIsNone(incident["resolved_at"])

    def test_the_french_title_is_kept_and_its_absence_is_none(self):
        data = _fresh_data()
        frans = {**self.SLOW, "title_fr": "L’API répond lentement"}
        _update_alert_incidents(data, self.BE, "be", [frans], "2026-10-13T10:20:00+00:00")
        _update_alert_incidents(data, self.NL, "nl", [self.SLOW], "2026-10-13T10:20:00+00:00")
        by_host = {i["host"]: i["title_fr"] for i in data["incidents"]}
        self.assertEqual(by_host, {self.BE: "L’API répond lentement", self.NL: None})

    def test_the_english_title_is_kept_and_its_absence_is_none(self):
        data = _fresh_data()
        engels = {**self.SLOW, "title_en": "The API is responding slowly"}
        _update_alert_incidents(data, self.NL, "nl", [engels], "2026-10-13T10:20:00+00:00")
        _update_alert_incidents(data, self.BE, "be", [self.SLOW], "2026-10-13T10:20:00+00:00")
        by_host = {i["host"]: i["title_en"] for i in data["incidents"]}
        self.assertEqual(by_host, {self.NL: "The API is responding slowly", self.BE: None})

    def test_no_answer_changes_nothing_and_an_answer_without_it_closes_it(self):
        data = _fresh_data()
        _update_alert_incidents(data, self.NL, "nl", [self.SLOW], "2026-10-13T10:20:00+00:00")
        _update_alert_incidents(data, self.NL, "nl", None, "2026-10-13T10:25:00+00:00")
        self.assertIsNone(data["incidents"][0]["resolved_at"])
        _update_alert_incidents(data, self.NL, "nl", [], "2026-10-13T10:30:00+00:00")
        self.assertEqual(data["incidents"][0]["resolved_at"], "2026-10-13T10:30:00+00:00")

    def test_each_host_closes_only_its_own(self):
        data = _fresh_data()
        _update_alert_incidents(data, self.NL, "nl", [self.SLOW], "2026-10-13T10:20:00+00:00")
        _update_alert_incidents(data, self.BE, "be", [self.SLOW], "2026-10-13T10:20:00+00:00")
        _update_alert_incidents(data, self.BE, "be", [], "2026-10-13T10:30:00+00:00")
        by_host = {i["host"]: i["resolved_at"] for i in data["incidents"]}
        self.assertEqual(by_host, {self.NL: None, self.BE: "2026-10-13T10:30:00+00:00"})

    def test_the_probes_own_incidents_are_never_closed_by_an_alert_answer(self):
        data = _fresh_data()
        data["incidents"].append(
            {"service": "api", "name": "API (Nederland)", "started_at": "2026-10-13T10:00:00+00:00",
             "resolved_at": None, "reason": None}
        )
        _update_alert_incidents(data, self.NL, "nl", [], "2026-10-13T10:30:00+00:00")
        self.assertIsNone(data["incidents"][0]["resolved_at"])

    def test_it_reaches_the_feed(self):
        data = _fresh_data()
        _update_alert_incidents(data, self.NL, "nl", [self.SLOW], "2026-10-13T10:20:00+00:00")
        (entry,) = _feed_entries(data["incidents"], [])
        self.assertEqual(entry["title"], "Storing: De API reageert traag (Nederland)")


class TestReasonCodes(unittest.TestCase):
    """status.json stores codes; the page renders them per language, the feed
    in Dutch (prijsprofeet#1568: the Belgian storefront gets French)."""

    def test_known_urllib_errors_get_a_code(self):
        self.assertEqual(probe.unreachable_reason("The read operation timed out"), "timeout")
        self.assertEqual(
            probe.unreachable_reason("<urlopen error [Errno 104] Connection reset by peer>"), "reset"
        )
        self.assertEqual(probe.unreachable_reason("weird"), "unreachable")

    def test_the_feed_renders_every_code_in_dutch(self):
        self.assertEqual(probe.reason_text("timeout"), "onbereikbaar: time-out")
        self.assertEqual(probe.reason_text("http_525"), "HTTP 525")
        self.assertIsNone(probe.reason_text(None))

    def test_every_code_the_checks_write_has_dutch(self):
        import inspect

        src = inspect.getsource(probe)
        written = set(__import__("re").findall(r'CheckResult\((?:True|False), "([a-z_]+)"\)', src))
        written |= {code for _p, code in probe.UNREACHABLE_REASONS} | {"unreachable", "not_fresh"}
        self.assertEqual(written - set(probe.REASON_TEXT), set())

    def test_the_page_knows_every_code_too(self):
        from pathlib import Path

        app = (Path(__file__).resolve().parent.parent / "app.js").read_text()
        for code in probe.REASON_TEXT:
            self.assertRegex(app, rf"\b{code}:", code)

    def test_a_row_stores_its_country_and_check(self):
        data = _fresh_data()
        _update_service(data, "mcp_be", "MCP-connector", "url", CheckResult(True, "ok"), "be")
        row = data["services"]["mcp_be"]
        self.assertEqual((row["country"], row["check"], row["reason"]), ("be", "mcp", "ok"))


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
        "reason": "timeout",
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

    def test_the_english_feed_speaks_english_with_the_same_ids(self):
        nl = self._parse(probe.build_feed([self.INCIDENT], []))
        en = self._parse(probe.build_feed([self.INCIDENT], [], "en"))
        entries = lambda root: list(root.iter(f"{{{probe.ATOM_NS}}}entry"))  # noqa: E731
        titles = [e.findtext(f"{{{probe.ATOM_NS}}}title") for e in entries(en)]
        self.assertEqual(titles, ["Resolved: API (Belgium)", "Outage: API (Belgium)"])
        ids = lambda root: [e.findtext(f"{{{probe.ATOM_NS}}}id") for e in entries(root)]  # noqa: E731
        self.assertEqual(ids(nl), ids(en))
        self.assertNotEqual(
            nl.findtext(f"{{{probe.ATOM_NS}}}id"), en.findtext(f"{{{probe.ATOM_NS}}}id")
        )
        xml = probe.build_feed([self.INCIDENT], [], "en")
        self.assertIn('xml:lang="en"', xml)
        self.assertIn("Started 7 October 00:35", xml)
        self.assertIn("after 5 min", xml)

    def test_the_english_feed_names_an_alert_and_a_chain_in_english(self):
        alert = dict(
            self.INCIDENT, service="alert:PublicAPISlow", kind="alert", country="be",
            title="De API reageert traag", title_en="The API is responding slowly",
        )
        chain = dict(
            self.INCIDENT, service="versheid:albert_heijn:x", kind="versheid",
            chain="Albert Heijn", country="nl", reason="not_fresh",
        )
        xml = probe.build_feed([alert, chain], [], "en")
        self.assertIn("Outage: The API is responding slowly (Belgium)", xml)
        self.assertIn("Not fresh: Albert Heijn (Netherlands)", xml)

    def test_a_notice_in_english_or_else_dutch(self):
        notice = dict(self.NOTICE, title_en="Maintenance", body_en="Briefly unreachable.")
        self.assertIn("Maintenance", probe.build_feed([], [notice], "en"))
        self.assertIn("Onderhoud", probe.build_feed([], [self.NOTICE], "en"))

    def test_every_reason_code_has_english_too(self):
        self.assertEqual(set(probe.REASON_TEXT), set(probe.REASON_TEXT_EN))

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
        _update_service(data, "api", "API", "url", CheckResult(True, "ok"))
        today = probe._local_day(datetime.now(timezone.utc))
        self.assertEqual([d["date"] for d in data["services"]["api"]["history"]], [today])


class TestMcpCheck(unittest.TestCase):
    def _answer(self, status, body, error=None):
        calls = []

        def fake(url, body_sent=None):
            calls.append((url, body_sent))
            return status, {}, body, error

        original = probe._fetch
        probe._fetch = fake
        self.addCleanup(setattr, probe, "_fetch", original)
        return calls

    def _tools(self, *names):
        return json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": n} for n in names]}}
        ).encode()

    def test_both_tools_listed_is_up(self):
        self._answer(200, self._tools("search", "fetch"))
        self.assertTrue(probe.check_mcp("https://h").ok)

    def test_it_asks_tools_list_and_never_calls_a_tool(self):
        # A tool call counts against the connector's daily cap; tools/list does not.
        calls = self._answer(200, self._tools("search", "fetch"))
        probe.check_mcp("https://h")
        url, sent = calls[0]
        self.assertEqual(url, "https://h/api/v1/mcp")
        self.assertEqual(json.loads(sent)["method"], "tools/list")

    def test_a_missing_tool_is_down(self):
        self._answer(200, self._tools("search"))
        result = probe.check_mcp("https://h")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "mcp_tools")

    def test_a_200_without_a_tool_list_is_down(self):
        self._answer(200, b"<html>maintenance</html>")
        self.assertFalse(probe.check_mcp("https://h").ok)

    def test_an_http_error_is_down(self):
        self._answer(502, b"")
        self.assertEqual(probe.check_mcp("https://h").reason, "http_502")

    def test_unreachable_is_a_code(self):
        self._answer(None, b"", "timed out")
        self.assertEqual(probe.check_mcp("https://h").reason, "timeout")


class TestCacheVersions(unittest.TestCase):
    """GitHub Pages serves app.js and styles.css with max-age=600, so a browser
    can pair a new index.html with an old app.js (seen 2026-10-18: the English
    feed link did not show). index.html names each file with a hash of its
    content; change the file, and this test names the new value."""

    def test_index_names_the_current_version_of_each_file(self):
        import hashlib
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        index = (root / "index.html").read_text()
        for name in ("app.js", "styles.css"):
            digest = hashlib.sha256((root / name).read_bytes()).hexdigest()[:10]
            self.assertIn(f"{name}?v={digest}", index, f"zet in index.html {name}?v={digest}")
