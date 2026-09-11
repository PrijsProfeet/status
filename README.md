# PrijsProfeet Status

External status page for prijsprofeet.nl — website and API — probed from
**outside** the box it reports on. See
[prijsprofeet#841](https://github.com/PrijsProfeet/prijsprofeet/issues/841)
for why: a status page hosted on the same infrastructure as the thing it
monitors answers "up" from inside the very outage it should report.

## How it works

- `collector/probe.py` (stdlib-only Python) hits `/` and `/api/v1/ready` on
  `www.prijsprofeet.nl`, plus the public `/api/v1/sla/summary` endpoint for
  the monthly SLA track record, and writes the result into `data/status.json`.
- `.github/workflows/probe.yml` runs that script every 5 minutes (best
  effort — GitHub can delay a scheduled run under load) and commits the
  updated JSON straight to `main`.
- `index.html` / `app.js` / `styles.css` render `data/status.json` — no
  framework, no build step, no network calls off this origin, so the page
  itself can't be taken down by anything failing elsewhere.
- Served by GitHub Pages at `status.prijsprofeet.nl`.

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
