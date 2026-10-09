/* PrijsProfeet status page — renders data/status.json, written by
 * collector/probe.py. No framework, no build, no network calls off this
 * origin: during the outages this page exists to report, anything it
 * fetched from elsewhere would be one more thing that can fail.
 */

const WINDOW_DAYS = 31;

// The collector commits new data roughly every 5 minutes; polling faster
// than that only spends the visitor's battery and GitHub Pages' bandwidth
// on refetching a file that hasn't changed. Polling slower would mean a
// visitor who leaves the tab open during an outage sits on a stale "Alles werkt"
// for longer than the collector itself needed.
const POLL_INTERVAL_MS = 60_000;
// The "Bijgewerkt N minuten geleden" line goes stale even between polls; refreshing
// just that text needs no network call at all.
const CLOCK_TICK_MS = 15_000;
// The collector runs every 5 minutes, and GitHub can delay a scheduled run.
// Past this age the data no longer says anything about now, so the banner
// must stop reporting a status: a stalled collector would otherwise leave the
// page green through the very outage it should show.
const STALE_AFTER_MINUTES = 20;

let lastData = null;
let lastFetchedAt = 0;

const STATUS_TEXT = {
  up: 'In orde',
  degraded: 'Verstoord',
  down: 'Storing',
  none: 'Geen gegevens',
};

const MONTHS = new Intl.DateTimeFormat('nl-NL', { month: 'long', year: 'numeric', timeZone: 'UTC' });

function monthLabel(month) {
  return MONTHS.format(new Date(`${month}-01T00:00:00Z`));
}

async function getJSON(path) {
  // Cache-busted: a reload is supposed to show the last few minutes, and a
  // CDN or browser holding a stale copy of this file is worse than no page.
  const res = await fetch(`${path}?t=${Date.now()}`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function ago(iso) {
  if (!iso) return 'nooit';
  const mins = minutesSince(iso);
  if (!Number.isFinite(mins)) return 'onbekend';
  if (mins < 1) return 'zojuist';
  if (mins === 1) return '1 minuut geleden';
  if (mins < 60) return `${mins} minuten geleden`;
  const hrs = Math.round(mins / 60);
  if (hrs === 1) return '1 uur geleden';
  if (hrs < 48) return `${hrs} uur geleden`;
  return `${Math.round(hrs / 24)} dagen geleden`;
}

function minutesSince(iso) {
  return Math.round((Date.now() - new Date(iso).getTime()) / 60000);
}

function isStale(data) {
  if (!data.generated_at) return true;
  const mins = minutesSince(data.generated_at);
  return !Number.isFinite(mins) || mins > STALE_AFTER_MINUTES;
}

function formatDay(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  return d.toLocaleDateString('nl-NL', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

// The UTC days an incident of this service touched. A day is only amber when
// an incident (INCIDENT_THRESHOLD consecutive failures in probe.py, ~10 min)
// fell on it: one timed-out check out of ~290 painted whole days amber while
// the incident list, by its own rule, stayed empty — two answers on one page.
function incidentDays(key, incidents) {
  const days = new Set();
  for (const incident of incidents || []) {
    if (incident.service !== key) continue;
    const start = new Date(incident.started_at);
    const end = incident.resolved_at ? new Date(incident.resolved_at) : new Date();
    const d = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate()));
    for (; d <= end; d.setUTCDate(d.getUTCDate() + 1)) days.add(d.toISOString().slice(0, 10));
  }
  return days;
}

function dayStatus(day, withIncident) {
  if (day.before) return 'before';
  if (!day.runs) return 'none';
  if (day.failed_runs === day.runs) return 'down';
  if (withIncident) return 'degraded';
  return 'up';
}

/* Index the stored history by date so the strip is built from the calendar,
 * not from the data. Days the collector never ran must show as gaps; drawing
 * only the days we have would silently compress an outage into a green run.
 * Days before a service's first check are a different thing — the host did
 * not exist yet (.be went live 2026-09-26) — and are marked as such rather
 * than shown as missing data. */
function buildStrip(history) {
  const byDate = new Map((history || []).map((d) => [d.date, d]));
  const first = (history || []).reduce((min, d) => (!min || d.date < min ? d.date : min), null);
  const days = [];
  const today = new Date();
  for (let i = WINDOW_DAYS - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    days.push(byDate.get(key) || { date: key, runs: 0, failed_runs: 0, before: !first || key < first });
  }
  return days;
}

// The tip of a bar near either edge is anchored to that edge, or it runs off
// a phone screen.
const EDGE_BARS = 6;

function renderStrip(key, history, incidents) {
  const withIncident = incidentDays(key, incidents);
  const bars = el('div', 'bars');
  const days = buildStrip(history);
  days.forEach((day, index) => {
    const bar = el('div', 'bar');
    const incident = withIncident.has(day.date);
    bar.dataset.status = dayStatus(day, incident);
    if (index < EDGE_BARS) bar.dataset.edge = 'start';
    if (index >= days.length - EDGE_BARS) bar.dataset.edge = 'end';

    const lines = [formatDay(day.date)];
    if (day.before) {
      lines.push('Nog niet gemeten');
    } else if (!day.runs) {
      lines.push('Geen checks vastgelegd');
    } else if (!day.failed_runs) {
      lines.push(`${day.runs} checks, alle geslaagd`);
    } else {
      lines.push(`${day.failed_runs} van ${day.runs} checks mislukt`);
      if (!incident) lines.push(day.failed_runs === 1 ? 'losse check, geen incident' : 'losse checks, geen incident');
      if (day.first_failure && day.first_failure.detail) {
        lines.push(day.first_failure.detail);
      }
    }
    // Focusable with a label, so the day is readable by keyboard, by screen
    // reader and by tapping on a phone (a tap focuses), not by hover alone.
    bar.tabIndex = 0;
    bar.setAttribute('role', 'img');
    bar.setAttribute('aria-label', lines.join(', '));
    const tip = el('div', 'tip');
    tip.setAttribute('aria-hidden', 'true');
    lines.forEach((line, i) => {
      if (i > 0) tip.appendChild(document.createElement('br'));
      tip.appendChild(document.createTextNode(line));
    });
    bar.appendChild(tip);
    bars.appendChild(bar);
  });
  return bars;
}

function serviceStatus(service) {
  // A status from a check that is no longer recent says nothing about now.
  if (!service.last_checked || minutesSince(service.last_checked) > STALE_AFTER_MINUTES) {
    return 'none';
  }
  return service.status || 'none';
}

function renderService(key, service, incidents) {
  const row = el('div', 'service');

  const head = el('div', 'service-head');
  const name = el('span', 'service-name', service.name);
  if (service.target) name.title = service.target;
  head.appendChild(name);
  const statusKey = serviceStatus(service);
  const status = el(
    'span',
    'service-status',
    statusKey === 'none' && service.last_checked ? 'Geen recente check' : STATUS_TEXT[statusKey],
  );
  status.dataset.status = statusKey;
  head.appendChild(status);
  row.appendChild(head);

  row.appendChild(renderStrip(key, service.history, incidents));
  row.appendChild(
    el(
      'div',
      'service-meta',
      `Gecontroleerd ${ago(service.last_checked)}${
        service.detail ? ` · ${service.detail}` : ''
      }`,
    ),
  );
  return row;
}

function overallStatus(services) {
  const statuses = Object.values(services).map((s) => s.status);
  if (statuses.every((s) => s === 'up')) return 'up';
  if (statuses.some((s) => s === 'down')) return 'down';
  if (statuses.length === 0) return 'none';
  return 'degraded';
}

function renderBanner(data) {
  const banner = document.getElementById('banner');
  const text = banner.querySelector('.banner-text');
  if (isStale(data)) {
    banner.dataset.status = 'none';
    text.textContent = data.generated_at
      ? `Gegevens verouderd: de laatste check was ${ago(data.generated_at)}`
      : 'Geen gegevens';
    return;
  }
  const status = overallStatus(data.services || {});
  banner.dataset.status = status;
  text.textContent = {
    up: 'Alles werkt',
    degraded: 'Gedeeltelijke storing',
    down: 'Storing',
    none: 'Geen gegevens',
  }[status];
}

// The Business SLA is owed per host and scored per host (#1242): one table
// each, never an average. .nl first; its history is the longest.
const SLA_HOSTS = ['www.prijsprofeet.nl', 'www.prijsprofeet.be'];

// Verbatim digits, only the decimal separator changes: rounding 99,9665 would
// make a figure the API states look more (or less) exact than it is.
function nlNumber(n) {
  return String(n).replace('.', ',');
}

function renderSlaTable(summary) {
  // Months before the probe existed (`measured: false`) will never carry a
  // number — that isn't "not yet", it's permanent, so they're dropped rather
  // than shown as a wall of "nog niet gemeten" rows that never resolve.
  const months = (summary.months || []).filter((m) => m.measured);
  if (!months.length) return null;

  const table = el('table', 'sla-table');
  const head = table.insertRow();
  ['Maand', 'Beschikbaarheid', 'Uitval', `Doel: ${nlNumber(summary.target_pct)}%`].forEach((h) => {
    const th = document.createElement('th');
    th.textContent = h;
    head.appendChild(th);
  });

  for (const m of months) {
    const row = table.insertRow();
    row.insertCell().textContent = slaMonthLabel(m);
    row.insertCell().textContent =
      m.availability_pct != null ? `${nlNumber(m.availability_pct)}%` : 'nog niet gemeten';
    // Minutes say more than a fourth decimal: "4 min" is what a partner weighs.
    row.insertCell().textContent =
      m.downtime_minutes == null
        ? '—'
        : m.downtime_minutes === 0
          ? 'geen'
          : formatDuration(m.downtime_minutes * 60000);
    const verdict = row.insertCell();
    // A running month's `met` can still flip before it closes — reporting
    // "gehaald" on it would claim a verdict the month hasn't earned yet.
    if (!m.closed) {
      verdict.textContent = 'loopt nog';
    } else if (m.met === true) {
      verdict.textContent = 'gehaald';
      verdict.className = 'met';
    } else if (m.met === false) {
      verdict.textContent = 'gemist';
      verdict.className = 'missed';
    } else {
      verdict.textContent = '—';
    }
  }
  return table;
}

// A month the probe did not cover from its first day (a host that went live
// mid-month, or the month the probe started) says so: 99,99% over five days
// is not the same claim as over a whole month. The running month is partial
// only because it has not ended, which "loopt nog" already says.
function slaMonthLabel(m) {
  const label = monthLabel(m.month);
  if (!m.partial || !m.measured_from) return label;
  const from = new Date(m.measured_from);
  const fromDay = from.toLocaleDateString('sv-SE', { timeZone: 'Europe/Amsterdam' });
  if (fromDay === `${m.month}-01`) return label;
  const shown = from.toLocaleDateString('nl-NL', {
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/Amsterdam',
  });
  return `${label} (gemeten vanaf ${shown})`;
}

function renderSla(slaByHost) {
  const section = document.getElementById('sla');
  const container = document.getElementById('sla-tables');
  container.innerHTML = '';
  const byHost = slaByHost || {};
  const hosts = Object.keys(byHost).sort(
    (a, b) => (SLA_HOSTS.indexOf(a) + 1 || 99) - (SLA_HOSTS.indexOf(b) + 1 || 99),
  );
  for (const host of hosts) {
    const table = renderSlaTable(byHost[host]);
    if (!table) continue;
    container.appendChild(el('h3', 'sla-host', host.replace(/^www\./, '')));
    container.appendChild(scrollable(table));
  }
  section.hidden = !container.children.length;
}

// A table wider than a phone scrolls inside its own box, never the page.
function scrollable(table) {
  const wrap = el('div', 'table-wrap');
  wrap.appendChild(table);
  return wrap;
}

// Freshness per chain (#1398): was each chain's data fresh at 07:00, and the
// share of fresh nights per calendar month against the norm. Per host, like
// the SLA: .be has its own chains. Only fresh or not, never the reason.
function todayAmsterdam() {
  // sv-SE formats as YYYY-MM-DD, the shape the API's `day` field uses.
  return new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Amsterdam' });
}

function pct(ratio) {
  // Floored, so 89,96% never reads as a met 90%.
  return `${(Math.floor(ratio * 1000) / 10).toLocaleString('nl-NL')}%`;
}

function sortedRetailers(summary) {
  return [...(summary.retailers || [])].sort((a, b) =>
    (a.name || a.retailer).localeCompare(b.name || b.retailer, 'nl'),
  );
}

// One line per host that answers the question a visitor comes with — was
// this morning's data in? — and names any chain that was not. The full table
// sits behind it: with every chain and month it is most of the page.
function freshnessSummary(summary) {
  const retailers = sortedRetailers(summary);
  const day = retailers.reduce(
    (max, r) => (r.latest && (!max || r.latest.day > max) ? r.latest.day : max),
    null,
  );
  if (!day) return null;
  const measured = retailers.filter((r) => r.latest && r.latest.day === day);
  const notFresh = measured.filter((r) => !r.latest.fresh).map((r) => r.name || r.retailer);
  const unmeasured = retailers
    .filter((r) => !r.latest || r.latest.day !== day)
    .map((r) => r.name || r.retailer);

  const when = day === todayAmsterdam() ? 'Vanochtend' : `Op ${formatDay(day)}`;
  const p = el('p', 'fresh-summary');
  const fresh = measured.length - notFresh.length;
  const lead = el('span', null, `${when}: ${fresh} van ${measured.length} ketens vers.`);
  lead.className = notFresh.length ? 'missed' : 'met';
  p.appendChild(lead);
  if (notFresh.length) p.appendChild(document.createTextNode(` Niet vers: ${notFresh.join(', ')}.`));
  if (unmeasured.length) {
    p.appendChild(document.createTextNode(` Nog geen meting: ${unmeasured.join(', ')}.`));
  }
  return p;
}

function renderFreshnessTable(summary) {
  const retailers = sortedRetailers(summary);
  if (!retailers.length) return null;
  const today = todayAmsterdam();
  const current = today.slice(0, 7);
  const months = [...new Set(retailers.flatMap((r) => (r.months || []).map((m) => m.month)))]
    .sort()
    .reverse();

  const table = el('table', 'sla-table');
  const head = table.insertRow();
  ['Keten', 'Om 07:00', ...months.map((m) => monthLabel(m) + (m === current ? ' (loopt nog)' : ''))].forEach(
    (h) => {
      const th = document.createElement('th');
      th.textContent = h;
      head.appendChild(th);
    },
  );

  for (const r of retailers) {
    const row = table.insertRow();
    row.insertCell().textContent = r.name || r.retailer;

    const latest = row.insertCell();
    if (!r.latest) {
      latest.textContent = 'nog niet gemeten';
    } else {
      latest.textContent = r.latest.fresh ? 'vers' : 'niet vers';
      // A morning that was not frozen yet must not read as today's answer.
      if (r.latest.day !== today) latest.textContent += ` (${formatDay(r.latest.day)})`;
      latest.className = r.latest.fresh ? 'met' : 'missed';
    }

    for (const month of months) {
      const cell = row.insertCell();
      const m = (r.months || []).find((x) => x.month === month);
      if (!m) {
        cell.textContent = '—';
        continue;
      }
      cell.textContent = `${pct(m.ratio)} (${m.fresh_nights}/${m.nights})`;
      // Same rule as the SLA: a running month has no verdict yet.
      if (month !== current) cell.className = m.meets_norm ? 'met' : 'missed';
    }
  }
  return table;
}

function renderFreshness(byHostData) {
  const section = document.getElementById('freshness');
  const container = document.getElementById('freshness-tables');
  container.innerHTML = '';
  const byHost = byHostData || {};
  const hosts = Object.keys(byHost).sort(
    (a, b) => (SLA_HOSTS.indexOf(a) + 1 || 99) - (SLA_HOSTS.indexOf(b) + 1 || 99),
  );
  for (const host of hosts) {
    const table = renderFreshnessTable(byHost[host]);
    if (!table) continue;
    container.appendChild(el('h3', 'sla-host', host.replace(/^www\./, '')));
    const summary = freshnessSummary(byHost[host]);
    if (summary) container.appendChild(summary);
    const details = el('details', 'fresh-details');
    details.appendChild(el('summary', null, 'Per keten en per maand'));
    details.appendChild(scrollable(table));
    container.appendChild(details);
  }
  section.hidden = !container.children.length;
}

function formatDuration(ms) {
  const mins = Math.round(ms / 60000);
  if (mins < 1) return 'minder dan een minuut';
  if (mins < 60) return `${mins} min`;
  const hrs = Math.floor(mins / 60);
  const rest = mins % 60;
  return rest ? `${hrs} u ${rest} min` : `${hrs} u`;
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleString('nl-NL', {
    timeZone: 'Europe/Amsterdam',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// Hand-written notices from notices.json (validated by probe.py). Only the
// ones in force now are shown; the feed keeps them all.
function renderNotices(notices) {
  const section = document.getElementById('notices');
  const now = Date.now();
  const current = (notices || []).filter(
    (n) => new Date(n.published) <= now && (!n.until || new Date(n.until) > now),
  );
  section.hidden = !current.length;
  section.innerHTML = '';
  for (const n of current) {
    const item = el('div', 'notice');
    item.appendChild(el('div', 'notice-title', n.title));
    item.appendChild(el('div', 'notice-body', n.body));
    item.appendChild(
      el(
        'div',
        'incident-meta',
        `Geplaatst ${formatDateTime(n.published)}${n.until ? ` · geldt tot ${formatDateTime(n.until)}` : ''}`,
      ),
    );
    section.appendChild(item);
  }
}

function renderIncidents(incidents) {
  const section = document.getElementById('incidents');
  const list = (incidents || []).slice(0, 20);
  if (!list.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;

  const ul = document.getElementById('incidents-list');
  ul.innerHTML = '';
  for (const incident of list) {
    const li = el('li', 'incident');
    const ongoing = incident.resolved_at === null;
    li.dataset.ongoing = String(ongoing);

    const head = el('div', 'incident-head');
    head.appendChild(document.createTextNode(incident.name || incident.service));
    if (ongoing) head.appendChild(el('span', null, 'Lopend'));
    li.appendChild(head);

    const start = formatDateTime(incident.started_at);
    const duration = ongoing
      ? `sinds ${start}`
      : `${start}, duurde ${formatDuration(
          new Date(incident.resolved_at) - new Date(incident.started_at),
        )}`;
    li.appendChild(el('div', 'incident-meta', `${duration}${
      incident.detail ? ` — ${incident.detail}` : ''
    }`));

    ul.appendChild(li);
  }
}

function renderData(data) {
  document.getElementById('checked').textContent = `Bijgewerkt ${ago(data.generated_at)}`;
  document.getElementById('build').textContent = data.generated_at
    ? `Laatste check: ${formatDateTime(data.generated_at)}`
    : '';

  renderBanner(data);

  const main = document.getElementById('services');
  main.innerHTML = '';
  // One page, both storefronts: rows grouped per country under a heading,
  // so a host-only outage (DNS, Cloudflare zone, certificate) reads as such.
  const order = ['website', 'api', 'website_be', 'api_be'];
  const rank = (key) => (order.includes(key) ? order.indexOf(key) : order.length);
  const keys = Object.keys(data.services || {}).sort((a, b) => rank(a) - rank(b));
  let group = null;
  let panel = null;
  for (const key of keys) {
    const service = data.services[key];
    if (!panel || service.group !== group) {
      group = service.group;
      if (group) main.appendChild(el('h2', 'group-title', group));
      panel = main.appendChild(el('div', 'panel'));
    }
    panel.appendChild(renderService(key, service, data.incidents));
  }

  renderNotices(data.notices);
  renderIncidents(data.incidents);
  renderSla(data.sla);
  renderFreshness(data.freshness);
}

/* Re-renders the "Bijgewerkt N geleden" line between polls, without a network
 * call — so the page doesn't need a fresh fetch just to stop lying about
 * how old the data on screen is. */
function tickClock() {
  if (!lastData) return;
  document.getElementById('checked').textContent = `Bijgewerkt ${ago(lastData.generated_at)}`;
  // The banner turns stale between polls too, without a new fetch.
  renderBanner(lastData);
}

async function refresh() {
  try {
    const data = await getJSON('data/status.json');
    lastData = data;
    lastFetchedAt = Date.now();
    renderData(data);
  } catch (err) {
    // Leave whatever last rendered successfully on screen — replacing a
    // real (if slightly stale) status with "could not load" on a single
    // failed poll would be a worse answer than the one already showing.
    if (!lastData) {
      document.getElementById('checked').textContent = 'Statusgegevens konden niet worden geladen';
    }
  }
}

function startPolling() {
  refresh();
  setInterval(tickClock, CLOCK_TICK_MS);
  setInterval(() => {
    if (document.visibilityState === 'visible') refresh();
  }, POLL_INTERVAL_MS);

  // A tab left in the background for a while is showing data far older than
  // POLL_INTERVAL_MS by the time it's looked at again — catch up the moment
  // it becomes visible instead of waiting for the next tick.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - lastFetchedAt > POLL_INTERVAL_MS) {
      refresh();
    }
  });
}

startPolling();
