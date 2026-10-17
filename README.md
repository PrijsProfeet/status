# PrijsProfeet Status

External status page for prijsprofeet.nl and prijsprofeet.be — website and
API — probed from
**outside** the box it reports on. See
[prijsprofeet#841](https://github.com/PrijsProfeet/prijsprofeet/issues/841)
for why: a status page hosted on the same infrastructure as the thing it
monitors answers "up" from inside the very outage it should report.

## How it works

- `collector/probe.py` (stdlib-only Python) hits `/`, `/api/v1/ready` and the
  MCP connector (`POST /api/v1/mcp` with `tools/list`, which must list `search`
  and `fetch`) on
  every host in its `SITES` table (`www.prijsprofeet.nl`, `www.prijsprofeet.be`),
  rendered as one group per country on a single page, plus the public `/api/v1/sla/summary` endpoint on
  each host for that host's monthly SLA track record (the Business SLA is
  owed per host, #1242; an answer that names another host is refused), and writes the result into `data/status.json`.
- It also asks each host for `/api/v1/status/incidents`: the Prometheus
  alerts prijsprofeet marks public (a slow API, failing requests, search
  down; #954), each with a Dutch title. Every such alert becomes an incident
  like the probe's own, on the page and in the feed, starting when Prometheus
  says it began. Pulled, never pushed: the box holds no key to this repo. A
  failed fetch leaves open alert incidents as they are; only an answer that no
  longer lists an alert closes it.
- `.github/workflows/probe.yml` runs that script every 5 minutes (best
  effort — GitHub can delay a scheduled run under load) and commits the
  updated JSON straight to `main`.
- The page has two audiences. A visitor picks a storefront (`?land=nl` /
  `?land=be`, remembered in localStorage, `nl-BE`/`fr-BE` browsers start on
  België) and sees only that one: website, the connector in Claude and
  ChatGPT, the freshness of its chains, its incidents, and a banner for that
  storefront alone. API customers get a fixed block below with the API row of
  each host and the Business SLA per host, outside the country choice.
- `status.json` stores **codes, never sentences** (`country`, `check`,
  `reason` such as `timeout` or `http_503`), so the page can render them in
  the visitor's language. Every sentence on the page lives in `STRINGS` in
  `app.js`; België speaks Dutch and French (prijsprofeet#1568): `STRINGS.fr`
  plus `'fr'` in `LANGS.be`, and the language switch shows (`?taal=fr`). An
  alert carries the app's Dutch `title` and French `title_fr` (`public_title_fr`
  in its alerts.yml); without the latter the French page shows the Dutch one.
  The Atom feed is Dutch and
  renders the same codes through `REASON_TEXT` in `probe.py`; a test holds
  both tables to the codes the checks write.
- `index.html` / `app.js` / `styles.css` render `data/status.json` — no
  framework, no build step, no network calls off this origin, so the page
  itself can't be taken down by anything failing elsewhere. The one
  exception is the Umami tracker (`pp-metrics.js` from www.prijsprofeet.nl,
  its own site id), loaded `async`: when the box is down it does not load and
  the page renders as before. Visits during an outage are therefore not
  counted.
- Served by GitHub Pages at `status.prijsprofeet.nl`.

### Why the MCP check never calls a tool

The connector counts every tool call against a daily cap, with a smaller cap
for third parties (prijsprofeet#914). A `search` every five minutes from two
hosts would use more than half of that smaller cap and show up in the
connector's usage metrics. `tools/list` counts for neither, and still proves
the nginx location, the transport and the tool registration. A connector that
is down while the site and the API answer turns the banner to "Gedeeltelijke
storing", not "Storing".

## Atom feed and notices

`probe.py` also writes `data/incidents.xml`, an Atom feed at
`https://status.prijsprofeet.nl/data/incidents.xml`: one entry when an
incident opens and a separate one when it is resolved (a feed reader notifies
on a new entry, not on an updated one), plus every notice. The feed only
changes when an entry does, never on the clock.

A chain that is not fresh at 07:00 (`/api/v1/freshness/summary` on each host)
is an incident too, per chain per host (`versheid:{retailer}:{host}`): "Niet
vers" on the first stale morning, "Weer vers" on the first fresh one. Both
ends sit at 07:00 of the measured day. The probe only sees the latest day, so
an incident that opens mid-streak (the first run, or after the probe was down)
starts on the day it was first seen; correct it by hand in `data/status.json`
from prod `freshness_days`. These incidents never turn the banner: our hosts
answer, the data stands still.

Notices are what the probe cannot see: planned maintenance, or a chain whose
source is down for a known reason. Add one to `notices.json` in a normal commit; the push runs
the workflow, which validates the file (the unit tests load it) and publishes
it within a minute or two:

```json
[
  {
    "id": "onderhoud-2026-10-12",
    "title": "Gepland onderhoud aan de API",
    "body": "Zondag tussen 07:00 en 07:15 kan de API kort onbereikbaar zijn.",
    "published": "2026-10-09T09:00:00+02:00",
    "until": "2026-10-12T07:15:00+02:00"
  }
]
```

`id` (lowercase, digits, hyphens) is the feed entry's identity: never reuse
or change it. `published` and `until` need an offset. The page shows a notice
from `published` until `until` (or forever without one); the feed keeps it.
Remove a notice from the file only once it is old news. A broken file keeps
the last valid notices published and turns the run red.

The feed is public on purpose. Everything in it is already on this page, and
a feed behind a key would have to run on the box whose outage it reports.
What Business customers get on top is a mail, sent to them (not built yet).

This is **not** the contractual SLA measurement. The Business-tier 99,5% is
measured by `blackbox-exporter` against the same `/api/v1/ready` endpoint,
scraped by Prometheus every 30s and scored in prijsprofeet's own
`app/services/sla_service.py` — that number is what's owed to partners. This
page exists only so *visitors* have somewhere to check that keeps working
when prijsprofeet.nl itself does not, and to show the same monthly figures
publicly.

### Why the website check isn't a real page-render test

Cloudflare's bot protection challenges every scripted client on `/` —
including this probe — with a 403 carrying `cf-mitigated: challenge`. That's
documented, expected behaviour for an automated caller (see prijsprofeet's
`CLAUDE.md`: "the HTML site cannot be blackbox-probed"), not an outage, so
`probe.py` counts it as reachable. A connection failure, a 5xx, or a 403
*without* that header is a real signal and counts as down. In short: this
check proves the edge (Cloudflare → nginx) is alive, not that the page
renders correctly for a real visitor.

## One-time setup (manual, outside this repo)

1. **DNS**: add a CNAME for `status.prijsprofeet.nl` → `prijsprofeet.github.io`,
   **DNS-only** (grey cloud) in Cloudflare — not proxied, so GitHub can issue
   and validate the TLS certificate for the custom domain.
2. **GitHub Pages**: repo Settings → Pages → source = `main` / `/` (root).
   Once the DNS above resolves, add `status.prijsprofeet.nl` as the custom
   domain and enable "Enforce HTTPS".
3. **Actions permissions**: repo Settings → Actions → General → Workflow
   permissions = "Read and write permissions" (needed for the scheduled job
   to commit `data/status.json`).
4. Nothing else — no secrets, no external accounts. Everything probed is a
   public, unauthenticated endpoint.

## Local testing

```
python3 collector/probe.py
python3 -m http.server 8080   # then open localhost:8080
```
