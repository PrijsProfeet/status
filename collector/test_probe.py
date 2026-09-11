#!/usr/bin/env python3
"""Unit tests for the incident-tracking logic in probe.py (#954).

Stdlib-only, run with `python3 -m unittest collector/test_probe.py` (or
`python3 collector/test_probe.py`) — no pytest dependency, matching probe.py's
own zero-dependency policy.
"""

import unittest
from datetime import datetime, timedelta, timezone

from probe import (
    INCIDENT_HISTORY_DAYS,
    INCIDENT_THRESHOLD,
    CheckResult,
    _prune_incidents,
    _update_service,
)


def _fresh_data():
    return {"generated_at": None, "services": {}, "sla": None, "incidents": []}


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


if __name__ == "__main__":
    unittest.main()
