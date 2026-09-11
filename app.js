/* PrijsProfeet status page — renders data/status.json, written by
 * collector/probe.py. No framework, no build, no network calls off this
 * origin: during the outages this page exists to report, anything it
 * fetched from elsewhere would be one more thing that can fail.
 */

const WINDOW_DAYS = 31;

const STATUS_TEXT = {
  up: 'Operational',
  degraded: 'Degraded',
  down: 'Outage',
  none: 'No data',
};

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
  if (!iso) return 'never';
  const then = new Date(iso);
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (!Number.isFinite(mins)) return 'unknown';
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 minute ago';
  if (mins < 60) return `${mins} minutes ago`;
  const hrs = Math.round(mins / 60);
  if (hrs === 1) return '1 hour ago';
  if (hrs < 48) return `${hrs} hours ago`;
  return `${Math.round(hrs / 24)} days ago`;
}

function formatDay(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

function dayStatus(day) {
  if (!day || !day.runs) return 'none';
  if (day.failed_runs === 0) return 'up';
  if (day.failed_runs === day.runs) return 'down';
  return 'degraded';
}

/* Index the stored history by date so the strip is built from the calendar,
 * not from the data. Days the collector never ran must show as gaps; drawing
 * only the days we have would silently compress an outage into a green run. */
function buildStrip(history) {
  const byDate = new Map((history || []).map((d) => [d.date, d]));
  const days = [];
  const today = new Date();
  for (let i = WINDOW_DAYS - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    days.push(byDate.get(key) || { date: key, runs: 0, failed_runs: 0 });
  }
  return days;
}

function uptimePct(history) {
  const days = buildStrip(history).filter((d) => d.runs > 0);
  if (!days.length) return null;
  const runs = days.reduce((sum, d) => sum + d.runs, 0);
  const failed = days.reduce((sum, d) => sum + d.failed_runs, 0);
  const pct = ((runs - failed) / runs) * 100;
  // Never round a real incident up to a flat 100 — see uptimeText below.
  return pct < 100 && pct > 99.99 ? 99.99 : pct;
}

function uptimeText(pct) {
  if (pct === null) return 'no data yet';
  return `${pct.toFixed(pct === 100 ? 0 : 2)}% uptime (last ${WINDOW_DAYS} days)`;
}

function renderStrip(history) {
  const bars = el('div', 'bars');
  for (const day of buildStrip(history)) {
    const bar = el('div', 'bar');
    const status = dayStatus(day);
    bar.dataset.status = status;

    const lines = [formatDay(day.date)];
    if (!day.runs) {
      lines.push('No checks recorded');
    } else if (!day.failed_runs) {
      lines.push(`${day.runs} checks, all passed`);
    } else {
      lines.push(`${day.failed_runs} of ${day.runs} checks failed`);
      if (day.first_failure && day.first_failure.detail) {
        lines.push(day.first_failure.detail);
      }
    }
    const tip = el('div', 'tip');
    lines.forEach((line, i) => {
      if (i > 0) tip.appendChild(document.createElement('br'));
      tip.appendChild(document.createTextNode(line));
    });
    bar.appendChild(tip);
    bars.appendChild(bar);
  }
  return bars;
}

function renderService(service) {
  const card = el('div', 'service');

  const head = el('div', 'service-head');
  head.appendChild(el('span', 'service-name', service.name));
  const status = el('span', 'service-status', STATUS_TEXT[service.status] || 'No data');
  status.dataset.status = service.status || 'none';
  head.appendChild(status);
  card.appendChild(head);

  const meta = el(
    'div',
    'service-meta',
    `${service.target || ''} · checked ${ago(service.last_checked)}${
      service.detail ? ` · ${service.detail}` : ''
    }`,
  );
  card.appendChild(meta);

  card.appendChild(renderStrip(service.history));
  card.appendChild(el('div', 'uptime', uptimeText(uptimePct(service.history))));

  return card;
}

function overallStatus(services) {
  const statuses = Object.values(services).map((s) => s.status);
  if (statuses.every((s) => s === 'up')) return 'up';
  if (statuses.some((s) => s === 'down')) return 'down';
  if (statuses.length === 0) return 'none';
  return 'degraded';
}

function renderBanner(services) {
  const banner = document.getElementById('banner');
  const status = overallStatus(services);
  banner.dataset.status = status;
  const text = {
    up: 'All systems operational',
    degraded: 'Partial outage',
    down: 'Major outage',
    none: 'No data',
  }[status];
  banner.querySelector('.banner-text').textContent = text;
}

function renderSla(sla) {
  const section = document.getElementById('sla');
  // Months before the probe existed (`measured: false`) will never carry a
  // number — that isn't "not yet", it's permanent, so they're dropped rather
  // than shown as a wall of "nog niet gemeten" rows that never resolve.
  const months = ((sla && sla.months) || []).filter((m) => m.measured);
  if (!months.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;

  const table = document.getElementById('sla-table');
  table.innerHTML = '';
  const head = table.insertRow();
  ['Maand', 'Beschikbaarheid', `Doel: ${sla.target_pct}%`].forEach((h) => {
    const th = document.createElement('th');
    th.textContent = h;
    head.appendChild(th);
  });

  for (const m of months) {
    const row = table.insertRow();
    row.insertCell().textContent = m.month;
    row.insertCell().textContent =
      m.availability_pct != null ? `${m.availability_pct}%` : 'nog niet gemeten';
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
}

async function render() {
  let data;
  try {
    data = await getJSON('data/status.json');
  } catch (err) {
    document.getElementById('checked').textContent = 'Could not load status data';
    return;
  }

  document.getElementById('checked').textContent = `Updated ${ago(data.generated_at)}`;
  document.getElementById('build').textContent = data.generated_at
    ? `Last probe run: ${new Date(data.generated_at).toISOString()}`
    : '';

  renderBanner(data.services || {});

  const main = document.getElementById('services');
  main.innerHTML = '';
  const order = ['website', 'api'];
  const keys = Object.keys(data.services || {}).sort(
    (a, b) => order.indexOf(a) - order.indexOf(b),
  );
  for (const key of keys) {
    main.appendChild(renderService(data.services[key]));
  }

  renderSla(data.sla);
}

render();
