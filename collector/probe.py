#!/usr/bin/env python3
"""Probes prijsprofeet.nl and prijsprofeet.be from OUTSIDE its own infrastructure and writes
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
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "status.json"
# Hand-written announcements (planned maintenance, a known source outage):
# what this probe can never see. Edited in the repo, validated here, shown on
# the page and in the feed.
NOTICES_FILE = ROOT / "notices.json"
FEED_FILE = ROOT / "data" / "incidents.xml"

SITE_URL = "https://status.prijsprofeet.nl/"
FEED_URL = SITE_URL + "data/incidents.xml"
# Newest entries kept in the feed. A reader only needs what it has not seen.
FEED_ENTRIES = 50
# The feed's <updated> when it holds no entries at all. A constant, never the
# run time: the feed must only change when an entry does, or every reader
# refetches it every five minutes and the bot commits it every run.
FEED_EPOCH = "2026-09-11T00:00:00+00:00"
ATOM_NS = "http://www.w3.org/2005/Atom"
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

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

# How long since the previous successful write before the PIPELINE itself
# (not the site) counts as stalled (#973). At a 5-minute cadence a healthy
# run's generated_at is always a few minutes old at most; a bigger gap means
# our own probe stopped running — e.g. a GitHub Actions job stuck in `queued`
# for 90+ minutes with nothing failing loudly about it. 3x the cadence
# tolerates one slow or skipped run without crying wolf.
STALL_THRESHOLD_MINUTES = 15

# One status page for both storefronts, not one per country: they run on the
# same box, so most outages hit both, and one page shows at a glance whether
# it is one host or all of them. What can break for ONE host alone -- its DNS,
# its Cloudflare zone, its certificate SAN, nginx `server_name` -- is exactly
# why each gets its own rows. (country label, base url, service-key suffix)
# NL keeps the unsuffixed keys so its history and incidents carry over.
SITES = [
    ("Nederland", "https://www.prijsprofeet.nl", ""),
    ("België", "https://www.prijsprofeet.be", "_be"),
]



# urllib's own wording, in Dutch. The page and the feed show what is stored,
# so the stored text is translated, once, here; older rows are translated on
# load (see _translate_details), so there is no second copy in app.js.
UNREACHABLE_REASONS = [
    (re.compile(r"timed out", re.I), "time-out"),
    (re.compile(r"connection reset", re.I), "verbinding verbroken"),
    (re.compile(r"connection refused", re.I), "verbinding geweigerd"),
    (re.compile(r"IncompleteRead"), "antwoord onvolledig"),
    (re.compile(r"name or service not known|nodename nor servname|getaddrinfo", re.I), "DNS-fout"),
    (re.compile(r"certificate|ssl", re.I), "TLS-fout"),
]
CLOUDFLARE_REACHABLE = "bereikbaar (Cloudflare houdt geautomatiseerde checks hier bewust tegen)"


def dutch_detail(detail: Optional[str]) -> Optional[str]:
    """Translate a check detail. Idempotent: a Dutch detail stays as it is."""
    if not detail:
        return detail
    if detail.startswith("reachable (Cloudflare"):
        return CLOUDFLARE_REACHABLE
    if detail.startswith("unreachable: "):
        reason = detail.removeprefix("unreachable: ")
        for pattern, dutch in UNREACHABLE_REASONS:
            if pattern.search(reason):
                return f"onbereikbaar: {dutch}"
        return f"onbereikbaar: {reason}"
    return detail


def _translate_details(data: dict[str, Any]) -> None:
    for service in data.get("services", {}).values():
        service["detail"] = dutch_detail(service.get("detail"))
        for day in service.get("history", []):
            if day.get("first_failure"):
                day["first_failure"]["detail"] = dutch_detail(day["first_failure"].get("detail"))
    for incident in data.get("incidents", []):
        incident["detail"] = dutch_detail(incident.get("detail"))


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
        return CheckResult(False, dutch_detail(f"unreachable: {error}"))
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
        return CheckResult(False, dutch_detail(f"unreachable: {error}"))
    if status == 200:
        return CheckResult(True, "200 OK")
    if status == 403 and "cf-mitigated" in {k.lower() for k in headers}:
        # Cloudflare challenges automated checks here by design.
        return CheckResult(True, CLOUDFLARE_REACHABLE)
    return CheckResult(False, f"HTTP {status}")


def fetch_sla_summary(base_url: str) -> Optional[dict[str, Any]]:
    """The monthly track record of ONE host. Best-effort: absent is a normal
    state (the endpoint may not exist yet, or Prometheus may be mid-recompute),
    never a reason to fail the whole run.

    The Business SLA is owed per host (API-voorwaarden art. 10, #1242) and the
    endpoint answers for the host it is asked on. An answer that does not name
    that host is refused: before #1242 every host answered with the .nl figures,
    and filing those under .be would publish a track record .be never had."""
    status, _headers, body, error = _fetch(f"{base_url}/api/v1/sla/summary?months=12")
    if error or status != 200:
        return None
    try:
        summary = json.loads(body)
    except json.JSONDecodeError:
        return None
    host = base_url.removeprefix("https://")
    if not isinstance(summary, dict) or summary.get("host") != host:
        return None
    return summary


def _update_sla(data: dict[str, Any], host: str, summary: Optional[dict[str, Any]]) -> None:
    """Store one host's track record under its own key. A failed fetch keeps
    what was last written — a hiccup must not blank months of SLA history.
    The pre-#1242 shape (one summary, a `months` key at the top) described .nl
    alone under no host, so it is dropped rather than guessed at."""
    sla = data.get("sla")
    if not isinstance(sla, dict) or "months" in sla:
        sla = data["sla"] = {}
    if summary is not None:
        sla[host] = summary


def fetch_freshness(base_url: str) -> Optional[dict[str, Any]]:
    """Freshness per chain on ONE host: was each chain's data fresh at 07:00,
    and the share of fresh nights per month against the 90% norm (#1398).
    Best-effort like the SLA summary: absent never fails the run.

    No host check as with the SLA: this endpoint answers for the host it is
    asked on from its first release, and its chains differ per host anyway."""
    status, _headers, body, error = _fetch(f"{base_url}/api/v1/freshness/summary?months=3")
    if error or status != 200:
        return None
    try:
        summary = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(summary, dict) or not isinstance(summary.get("retailers"), list):
        return None
    return summary


def _update_freshness(
    data: dict[str, Any], host: str, summary: Optional[dict[str, Any]]
) -> None:
    """Same rule as `_update_sla`: a failed fetch keeps the last answer."""
    freshness = data.get("freshness")
    if not isinstance(freshness, dict):
        freshness = data["freshness"] = {}
    if summary is not None:
        freshness[host] = summary


def _stall_minutes(previous_generated_at: Optional[str], now: datetime) -> Optional[float]:
    """Minutes since the last successful run, or None on the very first run
    ever (no previous data to compare against — not a stall)."""
    if not previous_generated_at:
        return None
    previous = datetime.fromisoformat(previous_generated_at)
    return (now - previous).total_seconds() / 60


def _local_day(instant: datetime) -> str:
    """The calendar day of an instant in the Netherlands and Belgium (one
    timezone, CET/CEST). History buckets are keyed on it, like the SLA and
    freshness months: an outage at 00:30 belongs to the day a reader calls
    that night, not to the UTC day before it.

    Buckets up to and including 2026-10-09 are UTC days and stay as they are:
    a run count cannot be split after the fact, and they age out of
    HISTORY_DAYS. The switch is exact (UTC 10-09 ended at local midnight);
    app.js's LOCAL_DAYS_FROM marks it for the incident colouring."""
    return instant.astimezone(AMSTERDAM).date().isoformat()


def _load() -> dict[str, Any]:
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text())
    else:
        data = {"generated_at": None, "services": {}, "sla": {}}
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
    data: dict[str, Any],
    key: str,
    name: str,
    target: str,
    result: CheckResult,
    group: str = "",
) -> None:
    service = data["services"].setdefault(
        key, {"name": name, "target": target, "history": [], "consecutive_failures": 0}
    )
    service["name"] = name
    service["group"] = group
    service["target"] = target
    service["status"] = "up" if result.ok else "down"
    service["detail"] = result.detail
    service["last_checked"] = datetime.now(timezone.utc).isoformat()

    now = datetime.now(timezone.utc)
    today = _local_day(now)
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

    cutoff = _local_day(now - timedelta(days=HISTORY_DAYS))
    service["history"] = [d for d in history if d["date"] >= cutoff]
    service["history"].sort(key=lambda d: d["date"])

    # An incident list mixes both countries, so it names the host too.
    incident_name = f"{name} ({group})" if group else name
    _update_incidents(data, key, incident_name, result, service["last_checked"])


class NoticeError(ValueError):
    """notices.json is not what this script can publish."""


NOTICE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _aware(value: Any, field: str, notice_id: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise NoticeError(f"notice {notice_id!r}: {field} is no ISO 8601 date-time") from None
    if parsed.tzinfo is None:
        # Without an offset, "09:00" is a guess between two timezones.
        raise NoticeError(f"notice {notice_id!r}: {field} needs an offset, e.g. +02:00")
    return parsed


def load_notices(path: Path = NOTICES_FILE) -> list[dict[str, Any]]:
    """The hand-written notices, validated. Raises NoticeError on anything a
    visitor or a feed reader would otherwise see half-broken."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise NoticeError(f"notices.json is no valid JSON: {e}") from None
    if not isinstance(raw, list):
        raise NoticeError("notices.json must be a list")
    seen: set[str] = set()
    notices = []
    for item in raw:
        if not isinstance(item, dict):
            raise NoticeError("every notice must be an object")
        notice_id = item.get("id")
        if not isinstance(notice_id, str) or not NOTICE_ID.match(notice_id):
            raise NoticeError(f"notice id {notice_id!r}: lowercase letters, digits and hyphens")
        if notice_id in seen:
            # The id is the feed entry's identity: a reader would drop the second.
            raise NoticeError(f"notice id {notice_id!r} appears twice")
        seen.add(notice_id)
        for field in ("title", "body"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise NoticeError(f"notice {notice_id!r}: {field} is required")
        published = _aware(item.get("published"), "published", notice_id)
        notice = {
            "id": notice_id,
            "title": item["title"].strip(),
            "body": item["body"].strip(),
            "published": published.isoformat(),
            "until": None,
        }
        if item.get("until") is not None:
            until = _aware(item["until"], "until", notice_id)
            if until <= published:
                raise NoticeError(f"notice {notice_id!r}: until lies before published")
            notice["until"] = until.isoformat()
        unknown = set(item) - {"id", "title", "body", "published", "until"}
        if unknown:
            raise NoticeError(f"notice {notice_id!r}: unknown field(s) {sorted(unknown)}")
        notices.append(notice)
    notices.sort(key=lambda n: n["published"], reverse=True)
    return notices


MONTHS_NL = [
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]


def _when(iso: str) -> str:
    """'7 oktober 00:35', in Amsterdam time — the time a Dutch or Belgian
    reader lives in, whatever the runner's clock says."""
    t = datetime.fromisoformat(iso).astimezone(AMSTERDAM)
    return f"{t.day} {MONTHS_NL[t.month - 1]} {t:%H:%M}"


def _duration(start: str, end: str) -> str:
    minutes = round(
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 60
    )
    if minutes < 1:
        return "minder dan een minuut"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} u {rest} min" if rest else f"{hours} u"


def _feed_entries(
    incidents: list[dict[str, Any]], notices: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """One entry when an incident opens and a SECOND when it is resolved: a
    feed reader notifies on a new id, not on an updated one, so a resolution
    folded into the first entry would never reach anyone."""
    entries = []
    for i in incidents:
        key = f"{i['service']}:{i['started_at']}"
        cause = f" Oorzaak volgens de check: {i['detail']}." if i.get("detail") else ""
        entries.append({
            "id": f"tag:status.prijsprofeet.nl,2026:incident:{key}:begin",
            "title": f"Storing: {i['name']}",
            "at": i["started_at"],
            "text": f"Begonnen {_when(i['started_at'])}.{cause}",
        })
        if i.get("resolved_at"):
            entries.append({
                "id": f"tag:status.prijsprofeet.nl,2026:incident:{key}:einde",
                "title": f"Opgelost: {i['name']}",
                "at": i["resolved_at"],
                "text": (
                    f"Opgelost {_when(i['resolved_at'])}, na "
                    f"{_duration(i['started_at'], i['resolved_at'])} "
                    f"(begonnen {_when(i['started_at'])})."
                ),
            })
    for n in notices:
        until = f" Geldt tot {_when(n['until'])}." if n["until"] else ""
        entries.append({
            "id": f"tag:status.prijsprofeet.nl,2026:mededeling:{n['id']}",
            "title": n["title"],
            "at": n["published"],
            "text": f"{n['body']}{until}",
        })
    entries.sort(key=lambda e: datetime.fromisoformat(e["at"]), reverse=True)
    return entries[:FEED_ENTRIES]


def build_feed(incidents: list[dict[str, Any]], notices: list[dict[str, Any]]) -> str:
    """An Atom feed of incidents and notices. Deterministic: the same input
    gives the same bytes, so the file only changes when an entry does."""
    ET.register_namespace("", ATOM_NS)

    def sub(parent: ET.Element, tag: str, text: Optional[str] = None, **attrs: str) -> ET.Element:
        node = ET.SubElement(parent, f"{{{ATOM_NS}}}{tag}", attrs)
        if text is not None:
            node.text = text
        return node

    entries = _feed_entries(incidents, notices)
    feed = ET.Element(f"{{{ATOM_NS}}}feed", {"xml:lang": "nl"})
    sub(feed, "id", SITE_URL)
    sub(feed, "title", "PrijsProfeet status: storingen en mededelingen")
    sub(feed, "updated", entries[0]["at"] if entries else FEED_EPOCH)
    sub(feed, "link", rel="self", href=FEED_URL)
    sub(feed, "link", rel="alternate", href=SITE_URL)
    author = sub(feed, "author")
    sub(author, "name", "PrijsProfeet")
    for e in entries:
        entry = sub(feed, "entry")
        sub(entry, "id", e["id"])
        sub(entry, "title", e["title"])
        sub(entry, "updated", e["at"])
        sub(entry, "published", e["at"])
        sub(entry, "link", rel="alternate", href=SITE_URL)
        sub(entry, "content", e["text"], type="text")
    ET.indent(feed)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(feed, encoding="unicode") + "\n"


def main() -> int:
    data = _load()
    previous_generated_at = data.get("generated_at")
    now = datetime.now(timezone.utc)
    data["generated_at"] = now.isoformat()

    for group, base_url, suffix in SITES:
        _update_service(
            data, "website" + suffix, "Website", base_url + "/",
            check_website(base_url), group,
        )
        _update_service(
            data, "api" + suffix, "API", base_url + "/api/v1/ready",
            check_api(base_url), group,
        )

    for _group, base_url, _suffix in SITES:
        host = base_url.removeprefix("https://")
        _update_sla(data, host, fetch_sla_summary(base_url))
        _update_freshness(data, host, fetch_freshness(base_url))

    _prune_incidents(data)
    _translate_details(data)

    # A broken notices.json must not take the monitoring down with it: the
    # last valid notices stay, the run still writes, and only the exit code
    # (further down) turns the job red so someone fixes the file.
    notice_error = None
    try:
        data["notices"] = load_notices()
    except NoticeError as e:
        notice_error = str(e)
        data.setdefault("notices", [])

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    FEED_FILE.write_text(build_feed(data["incidents"], data["notices"]))

    failures = [
        s["name"] for s in data["services"].values() if s["status"] != "up"
    ]
    if failures:
        print(f"DOWN: {', '.join(failures)}", file=sys.stderr)
    else:
        print("all services up")

    # Both checks report before either decides the exit code, so one red run
    # names every problem instead of hiding the second behind the first.
    exit_code = 0
    stall = _stall_minutes(previous_generated_at, now)
    if stall is not None and stall > STALL_THRESHOLD_MINUTES:
        print(
            f"STALLED PIPELINE: {stall:.0f} min since the last successful run "
            f"(threshold {STALL_THRESHOLD_MINUTES}) — this run's data was "
            "still written above; failing only to surface the gap (#973).",
            file=sys.stderr,
        )
        exit_code = 1

    if notice_error:
        print(
            f"INVALID notices.json: {notice_error} — the previous notices stay published.",
            file=sys.stderr,
        )
        exit_code = 1

    # Never non-zero on a real SITE outage — reporting that is the point of
    # this whole script. A stalled pipeline and a broken notices.json are our
    # own tooling failing, not prijsprofeet.nl being down.
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
