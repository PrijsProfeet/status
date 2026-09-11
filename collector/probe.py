#!/usr/bin/env python3
"""Probes prijsprofeet.nl from OUTSIDE its own infrastructure and writes
data/status.json — see #841 (statuspagina buiten de box).

Deliberately stdlib-only (urllib), so this needs no `pip install` step and
cannot break because a dependency's CI-only wheel disappeared. It is meant to
run every few minutes from GitHub Actions, i.e. a network and a provider this
site's own box has no influence over.

This is NOT the contractual SLI. The Business-tier 99,5% is measured by
blackbox-exporter against the same /api/v1/ready endpoint, from Prometheus,
and scored in prijsprofeet's own app/services/sla_service.py — that number is
what's owed to partners. This script exists only to give *visitors* a status
page that keeps working when the box it reports on does not.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "status.json"

TIMEOUT_SECONDS = 15
USER_AGENT = "prijsprofeet-status/1.0 (+https://github.com/PrijsProfeet/status)"

# How many days of daily buckets to keep. The page only ever shows the last 31
# (see app.js WINDOW_DAYS), but a little slack means a late/skipped run can
# never make the strip appear to lose a day it actually has data for.
HISTORY_DAYS = 45

# Consecutive failed checks before a blip becomes a reported incident (see
# #954). At a 5-minute cadence this is ~10 minutes down — long enough that a
# single transient timeout doesn't open (and immediately close) an "incident"
# on every network hiccup, short enough that a real outage is still caught
# well inside its own window. This only ever sees what THIS probe checks
# (reachability of / and /api/v1/ready) — it has no view of the internal
# Prometheus alerts (EANCoverageDrop, ForecastMiscalibrated, ...); wiring
# those in is the Alertmanager-bridge half of #954, deliberately not built
# here (would need a new credential on the box for no clear benefit yet).
INCIDENT_THRESHOLD = 2

# Keep a long tail of resolved incidents so a visitor can see the track
# record, not just what's happening right now.
INCIDENT_HISTORY_DAYS = 180


@dataclass
class CheckResult:
    ok: bool
    detail: str


def _fetch(url: str) -> tuple[Optional[int], dict[str, str], bytes, Optional[str]]:
    """GET url, returning (status_code, headers, body, error). Never raises.

    urllib treats a 4xx/5xx as an HTTPError instead of a normal response, so
    both branches are handled here rather than by the caller.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, dict(resp.headers), resp.read(), None
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(), None
    except Exception as e:  # noqa: BLE001 — a probe must never crash the run
        return None, {}, b"", str(e)


def check_api(base_url: str) -> CheckResult:
    """The API's own readiness probe — the same one the internal SLI hits."""
    status, _headers, _body, error = _fetch(f"{base_url}/api/v1/ready")
    if error:
        return CheckResult(False, f"unreachable: {error}")
    if status == 200:
        return CheckResult(True, "200 OK")
    return CheckResult(False, f"HTTP {status}")


def check_website(base_url: str) -> CheckResult:
    """The homepage.

    Cloudflare's bot protection challenges every scripted client on `/` —
    including this one — with a 403 carrying `cf-mitigated: challenge`. That
    is documented, expected behaviour for an automated caller, not an outage
    (see CLAUDE.md: "the HTML site cannot be blackbox-probed"), so it counts
    as reachable. Anything else — a connection failure, a 5xx, or a 403
    *without* that header — is a real signal and counts as down.
    """
    status, headers, _body, error = _fetch(base_url + "/")
    if error:
        return CheckResult(False, f"unreachable: {error}")
    if status == 200:
        return CheckResult(True, "200 OK")
    if status == 403 and "cf-mitigated" in {k.lower() for k in headers}:
        return CheckResult(
            True, "reachable (Cloudflare challenges automated checks here by design)"
        )
    return CheckResult(False, f"HTTP {status}")


def fetch_sla_summary(base_url: str) -> Optional[dict[str, Any]]:
    """The monthly track record. Best-effort: absent is a normal state (the
    endpoint may not exist yet, or Prometheus may be mid-recompute), never a
    reason to fail the whole run."""
    status, _headers, body, error = _fetch(f"{base_url}/api/v1/sla/summary?months=12")
    if error or status != 200:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _load() -> dict[str, Any]:
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text())
    else:
        data = {"generated_at": None, "services": {}, "sla": None}
    data.setdefault("incidents", [])
    return data


def _update_incidents(
    data: dict[str, Any], key: str, name: str, result: CheckResult, checked_at: str
) -> None:
    """Open an incident after INCIDENT_THRESHOLD consecutive failures on this
    service, and close it on the first success afterwards.

    `consecutive_failures` lives on the service dict so it survives between
    runs (each run is a fresh process); the incident itself is a separate,
    append-only list so a resolved incident's start time never gets rewritten
    by a later run touching the same service.
    """
    service = data["services"][key]
    streak = service.get("consecutive_failures", 0)

    if result.ok:
        if streak >= INCIDENT_THRESHOLD:
            ongoing = next(
                (
                    i
                    for i in data["incidents"]
                    if i["service"] == key and i["resolved_at"] is None
                ),
                None,
            )
            if ongoing is not None:
                ongoing["resolved_at"] = checked_at
        service["consecutive_failures"] = 0
        return

    streak += 1
    service["consecutive_failures"] = streak

    if streak == INCIDENT_THRESHOLD:
        data["incidents"].append(
            {
                "service": key,
                "name": name,
                "started_at": checked_at,
                "resolved_at": None,
                "detail": result.detail,
            }
        )
    elif streak > INCIDENT_THRESHOLD:
        ongoing = next(
            (
                i
                for i in data["incidents"]
                if i["service"] == key and i["resolved_at"] is None
            ),
            None,
        )
        if ongoing is not None:
            ongoing["detail"] = result.detail  # keep the latest failure reason


def _prune_incidents(data: dict[str, Any]) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=INCIDENT_HISTORY_DAYS)
    data["incidents"] = [
        i
        for i in data["incidents"]
        # Never drop an incident that's still open, however old it started —
        # dropping it would silently "resolve" an ongoing outage by omission.
        if i["resolved_at"] is None
        or datetime.fromisoformat(i["started_at"]) >= cutoff
    ]
    data["incidents"].sort(key=lambda i: i["started_at"], reverse=True)


def _update_service(
    data: dict[str, Any], key: str, name: str, target: str, result: CheckResult
) -> None:
    service = data["services"].setdefault(
        key, {"name": name, "target": target, "history": [], "consecutive_failures": 0}
    )
    service["name"] = name
    service["target"] = target
    service["status"] = "up" if result.ok else "down"
    service["detail"] = result.detail
    service["last_checked"] = datetime.now(timezone.utc).isoformat()

    today = _today_utc()
    history: list[dict[str, Any]] = service["history"]
    day = next((d for d in history if d["date"] == today), None)
    if day is None:
        day = {"date": today, "runs": 0, "failed_runs": 0, "first_failure": None}
        history.append(day)

    day["runs"] += 1
    if not result.ok:
        day["failed_runs"] += 1
        if day["first_failure"] is None:
            day["first_failure"] = {
                "time": service["last_checked"],
                "detail": result.detail,
            }

    cutoff = (datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)).date().isoformat()
    service["history"] = [d for d in history if d["date"] >= cutoff]
    service["history"].sort(key=lambda d: d["date"])

    _update_incidents(data, key, name, result, service["last_checked"])


def main() -> int:
    base_url = "https://www.prijsprofeet.nl"
    data = _load()
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    _update_service(data, "website", "Website", base_url + "/", check_website(base_url))
    _update_service(
        data, "api", "API", base_url + "/api/v1/ready", check_api(base_url)
    )

    sla = fetch_sla_summary(base_url)
    if sla is not None:
        data["sla"] = sla
    # else: keep whatever was last written — a fetch hiccup must not blank
    # months of persisted SLA history off the page.

    _prune_incidents(data)

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    failures = [
        s["name"] for s in data["services"].values() if s["status"] != "up"
    ]
    if failures:
        print(f"DOWN: {', '.join(failures)}", file=sys.stderr)
    else:
        print("all services up")
    return 0  # never fail the workflow on a real outage — that's the point


if __name__ == "__main__":
    raise SystemExit(main())
