/* Census Explorer — local browser interface.
 *
 * No framework, no bundler, no CDN. The page talks only to the local service,
 * which holds no credentials and performs no network retrieval.
 *
 * Two rules shape the rendering code:
 *   1. An unavailable value is drawn as "no data" with its stated reason. It is
 *      never drawn as zero and never sorted as zero.
 *   2. Units and denominators are shown with every number, because a share
 *      without its denominator is not a measurement.
 */
'use strict';

const state = {
  status: null,
  releaseId: null,
  compareId: '',
  level: 'county',
  measureId: null,
  dataset: null,
  datasetB: null,
  values: null,
  valuesB: null,
  geo: null,
  geoB: null,
  cuts: [],
  compat: null,
  topics: new Set(),
  search: '',
  sort: { key: 'estimate', dir: 'desc' },
  selected: null,
};

const SEQUENTIAL = ['#e8eef4', '#bcd0e2', '#89aecb', '#5386ad', '#27618e'];
const $ = (id) => document.getElementById(id);

async function api(path) {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  const body = await res.json().catch(() => ({ error: 'response was not JSON' }));
  if (!res.ok) {
    const err = new Error(body.error || `request failed (${res.status})`);
    err.kind = body.kind;
    err.status = res.status;
    throw err;
  }
  return body;
}

async function post(path, payload) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({ error: 'response was not JSON' }));
  if (!res.ok) throw new Error(body.error || `request failed (${res.status})`);
  return body;
}

function showBlock(title, message) {
  const el = $('compat');
  el.hidden = false;
  el.className = 'compat blocked';
  el.innerHTML = `<div><strong>${escapeHtml(title)}</strong></div>` +
    `<ul><li>${escapeHtml(message).replace(/\n/g, '<br>')}</li></ul>`;
}

function toast(message, ms = 4200) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

function fmt(value, unit) {
  if (value === null || value === undefined) return '—';
  if (unit === 'percent') return `${value.toFixed(1)}%`;
  return Math.round(value).toLocaleString('en-US');
}

function unitWord(measure) {
  return measure.unit === 'percent' ? '% of the denominator below' : 'persons';
}

function measureById(id) {
  return (state.dataset?.measures || []).find((m) => m.measure_id === id);
}

/* ---------------------------------------------------------------- boot */

async function boot() {
  state.status = await api('/api/status');
  if (state.status.data_mode === 'fixture') {
    const b = $('mode-banner');
    b.hidden = false;
    b.textContent =
      'FIXTURE MODE — every value shown is synthetic test data, and the shapes are ' +
      'generated rectangles, not boundaries. Nothing here is a census finding.';
  }
  const releases = state.status.releases;
  const sel = $('release-select');
  const cmp = $('compare-select');
  releases.forEach((r) => {
    sel.add(new Option(r.period_label, r.release_id));
    cmp.add(new Option(r.period_label, r.release_id));
  });
  state.releaseId = releases.some((r) => r.release_id === state.status.default_release)
    ? state.status.default_release : releases[0].release_id;
  sel.value = state.releaseId;

  sel.addEventListener('change', async () => {
    state.releaseId = sel.value;
    if (state.compareId === state.releaseId) { state.compareId = ''; cmp.value = ''; }
    await loadRelease();
  });
  cmp.addEventListener('change', async () => {
    state.compareId = cmp.value;
    await loadRelease();
  });
  $('level-select').addEventListener('change', async () => {
    state.level = $('level-select').value;
    state.selected = null;
    await loadGeography();
    // The comparison report and the class breaks are level-specific, so the
    // measure must be reloaded rather than just redrawn.
    await loadMeasure();
  });
  $('catalog-search').addEventListener('input', (e) => {
    state.search = e.target.value.trim().toLowerCase();
    renderCatalog();
  });
  $('btn-details').addEventListener('click', () => toggleDrawer());
  $('drawer-close').addEventListener('click', () => toggleDrawer(false));
  $('btn-export').addEventListener('click', doExport);
  $('save-form').addEventListener('submit', saveProject);

  await loadRelease();
}

async function loadRelease() {
  state.dataset = await api(`/api/dataset?release=${encodeURIComponent(state.releaseId)}`);
  state.datasetB = state.compareId
    ? await api(`/api/dataset?release=${encodeURIComponent(state.compareId)}`) : null;

  const r = state.dataset.release;
  $('period-note').textContent =
    `${r.product_label}. Reference period ${r.period_label} — a ${r.period_years}-year ` +
    `period estimate, not a single-year count. Geography vintage: ${r.geography_vintage}. ` +
    `Dataset key: ${r.dataset_key}.`;

  const levels = new Set(state.dataset.areas.map((a) => a.level));
  const levelSel = $('level-select');
  Array.from(levelSel.options).forEach((o) => { o.disabled = !levels.has(o.value); });
  if (!levels.has(state.level)) state.level = levelSel.value = [...levels][0];

  if (!state.measureId || !measureById(state.measureId)) {
    state.measureId = (state.dataset.measures.find((m) => m.measure_id === 'foreign_born_share')
      || state.dataset.measures[0]).measure_id;
  }
  renderTopics();
  renderCatalog();
  await loadGeography();
  await loadMeasure();
  renderFooter();
  renderProjects();
}

async function loadGeography() {
  state.geo = await api(
    `/api/geography?release=${encodeURIComponent(state.releaseId)}&level=${state.level}`);
  state.geoB = state.compareId
    ? await api(`/api/geography?release=${encodeURIComponent(state.compareId)}&level=${state.level}`)
    : null;
}

async function loadMeasure() {
  const q = `release=${encodeURIComponent(state.releaseId)}&measure=${encodeURIComponent(state.measureId)}`;
  state.values = (await api(`/api/values?${q}`)).values;
  state.compat = null;
  state.valuesB = null;
  if (state.compareId) {
    const qb = `release=${encodeURIComponent(state.compareId)}&measure=${encodeURIComponent(state.measureId)}`;
    state.valuesB = (await api(`/api/values?${qb}`)).values;
    state.compat = await api(
      `/api/compare?a=${encodeURIComponent(state.releaseId)}&b=${encodeURIComponent(state.compareId)}` +
      `&level=${state.level}&measure=${encodeURIComponent(state.measureId)}`);
  }
  computeCuts();
  render();
}

/* ------------------------------------------------------------- catalog */

function renderTopics() {
  const counts = new Map();
  state.dataset.measures.forEach((m) => (m.topics || []).forEach(
    (t) => counts.set(t, (counts.get(t) || 0) + 1)));
  const wrap = $('topic-chips');
  wrap.textContent = '';
  [...counts.keys()].sort().forEach((topic) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.textContent = `${topic} (${counts.get(topic)})`;
    b.setAttribute('aria-pressed', state.topics.has(topic) ? 'true' : 'false');
    b.addEventListener('click', () => {
      state.topics.has(topic) ? state.topics.delete(topic) : state.topics.add(topic);
      b.setAttribute('aria-pressed', state.topics.has(topic) ? 'true' : 'false');
      renderCatalog();
    });
    wrap.appendChild(b);
  });
}

function catalogMatches() {
  return state.dataset.measures.filter((m) => {
    if (state.topics.size && !(m.topics || []).some((t) => state.topics.has(t))) return false;
    if (!state.search) return true;
    const hay = [m.measure_id, m.label, m.concept, m.definition_note, m.universe_note,
      (m.topics || []).join(' '), (m.tables || []).join(' '),
      (m.numerator_cells || []).join(' ')].join(' ').toLowerCase();
    return hay.includes(state.search);
  });
}

function renderCatalog() {
  const list = $('measure-list');
  const matches = catalogMatches();
  list.textContent = '';
  matches.forEach((m) => {
    const li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.setAttribute('tabindex', '0');
    li.setAttribute('aria-selected', m.measure_id === state.measureId ? 'true' : 'false');
    const available = Object.values(m.availability || {}).some(Boolean);
    li.innerHTML =
      `<span class="m-label">${escapeHtml(m.label)}</span>` +
      `<span class="m-meta">${m.unit === 'percent' ? 'share (%)' : 'count (persons)'}` +
      ` · ${escapeHtml((m.tables || []).join(', '))}` +
      `${available ? '' : ' · unavailable in this release'}</span>`;
    const choose = () => { state.measureId = m.measure_id; renderCatalog(); loadMeasure(); };
    li.addEventListener('click', choose);
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); }
    });
    list.appendChild(li);
  });
  $('catalog-count').textContent =
    `${matches.length} of ${state.dataset.measures.length} measures` +
    `${state.search || state.topics.size ? ' match the filters' : ''}`;
}

/* ------------------------------------------------------------ rendering */

function areasAtLevel(dataset) {
  const areas = dataset.areas.filter((a) => a.level === state.level);
  // When a comparison is on, the view shows exactly the areas the comparison
  // is allowed to use. The disclosure says non-shared areas are excluded, so
  // they are excluded here too, not only in the sentence.
  if (state.compat && state.compat.allowed) {
    const allowed = new Set(state.compat.comparable_geoids || []);
    return areas.filter((a) => allowed.has(a.geoid));
  }
  return areas;
}

function selectedGeoids() {
  return areasAtLevel(state.dataset).map((a) => a.geoid);
}

function usableValues(values) {
  return areasAtLevel(state.dataset)
    .map((a) => values?.[a.geoid])
    .filter((v) => v && v.es === 'ok')
    .map((v) => v.e);
}

function quantileCuts(data, classes = 5) {
  const sorted = [...data].sort((a, b) => a - b);
  if (!sorted.length) return [];
  const cuts = [];
  for (let i = 1; i < classes; i += 1) {
    const pos = (i * (sorted.length - 1)) / classes;
    const lo = Math.floor(pos);
    const hi = Math.min(lo + 1, sorted.length - 1);
    cuts.push(sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo));
  }
  return cuts.filter((c, i, arr) => i === 0 || c > arr[i - 1]);
}

function computeCuts() {
  if (state.compat && state.compat.allowed && state.compat.shared_cut_points?.length) {
    state.cuts = state.compat.shared_cut_points;
    return;
  }
  state.cuts = quantileCuts(usableValues(state.values), 5);
}

function classOf(value) {
  if (value === null || value === undefined) return null;
  for (let i = 0; i < state.cuts.length; i += 1) if (value < state.cuts[i]) return i;
  return state.cuts.length;
}

function render() {
  const m = measureById(state.measureId);
  if (!m) return;
  const r = state.dataset.release;
  $('measure-title').textContent = m.label;
  $('measure-sub').textContent =
    `${m.unit === 'percent' ? 'Share' : 'Count'} · unit: ${unitWord(m)} · ` +
    `universe: ${m.universe_published?.[0] || m.universe_note} · ${r.period_label}`;
  $('map-caption').textContent =
    `${m.label} — ${r.period_label}, ${state.level === 'county' ? 'boroughs' : 'census tracts'}`;
  $('btn-figure').href =
    `/api/figure?kind=map&release=${encodeURIComponent(state.releaseId)}` +
    `&level=${state.level}&measure=${encodeURIComponent(state.measureId)}` +
    (state.compareId && state.compat?.allowed ? `&compare=${encodeURIComponent(state.compareId)}` : '');

  renderCompat();
  const visible = new Set(selectedGeoids());
  const geo = state.geo && {
    ...state.geo,
    features: state.geo.features.filter((f) => visible.has(f.properties.GEOID)),
  };
  drawMap($('map'), geo, state.values, m);
  const wrapB = $('map-b-wrap');
  if (state.compareId && state.compat?.allowed) {
    wrapB.hidden = false;
    $('map-b-caption').textContent =
      `${m.label} — ${state.datasetB.release.period_label} (same class breaks)`;
    drawMap($('map-b'), state.geoB && {
      ...state.geoB,
      features: state.geoB.features.filter((f) => visible.has(f.properties.GEOID)),
    }, state.valuesB, m);
  } else {
    wrapB.hidden = true;
  }
  renderLegend(m);
  renderTable(m);
  if (!$('drawer').hidden) renderDrawerBody();
}

function renderCompat() {
  const el = $('compat');
  if (!state.compat) { el.hidden = true; return; }
  const c = state.compat;
  el.hidden = false;
  el.className = 'compat' + (c.allowed ? '' : ' blocked');
  const items = [];
  if (!c.allowed) {
    items.push('<strong>This comparison is blocked, so only ' +
      `${escapeHtml(state.dataset.release.period_label)} is shown.</strong>`);
    c.blocking.forEach((b) => items.push(escapeHtml(b)));
    items.push('Try the borough level, or turn the comparison off, or record a ' +
      'reviewed equivalence in <code>config/geography_equivalence.json</code>.');
  }
  c.disclosures.forEach((d) => items.push(escapeHtml(d)));
  if (c.allowed && c.shared_cut_points?.length) {
    items.push('Both maps use one shared set of class breaks, computed over the pooled ' +
      'values of both periods.');
  }
  const ev = c.geography_evidence;
  const head = `Comparison check: ${escapeHtml(c.release_a)} vs ${escapeHtml(c.release_b)} — ` +
    `${c.shared_geoids} shared identifiers, ` +
    `${c.comparable_geoid_count} comparable` +
    (ev ? `, geography evidence: ${escapeHtml(ev.kind)} (${ev.established ? 'established' : 'not established'})` : '');
  el.innerHTML = `<div>${head}</div><ul><li>${items.join('</li><li>')}</li></ul>`;
}

function projectPath(geometry, project) {
  const out = [];
  const ring = (coords) => {
    const pts = coords.map(([x, y]) => project(x, y));
    if (pts.length) out.push('M' + pts.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join('L') + 'Z');
  };
  if (geometry.type === 'Polygon') geometry.coordinates.forEach(ring);
  else geometry.coordinates.forEach((poly) => poly.forEach(ring));
  return out.join(' ');
}

function drawMap(host, geo, values, measure) {
  host.textContent = '';
  if (!geo || !geo.features.length) {
    host.innerHTML = '<p class="muted small" style="padding:16px">' +
      'No boundary layer is available for this geography and vintage.</p>';
    return;
  }
  let minx = Infinity; let miny = Infinity; let maxx = -Infinity; let maxy = -Infinity;
  const walk = (n) => {
    if (typeof n[0] === 'number') {
      minx = Math.min(minx, n[0]); maxx = Math.max(maxx, n[0]);
      miny = Math.min(miny, n[1]); maxy = Math.max(maxy, n[1]);
    } else n.forEach(walk);
  };
  geo.features.forEach((f) => f.geometry && walk(f.geometry.coordinates));

  const W = 700; const H = 470; const pad = 10;
  const sx = Math.cos(((miny + maxy) / 2) * Math.PI / 180);
  const scale = Math.min((W - 2 * pad) / ((maxx - minx) * sx), (H - 2 * pad) / (maxy - miny));
  const ox = pad + ((W - 2 * pad) - (maxx - minx) * sx * scale) / 2;
  const oy = pad + ((H - 2 * pad) - (maxy - miny) * scale) / 2;
  const project = (x, y) => [ox + (x - minx) * sx * scale, oy + (maxy - y) * scale];

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label',
    `${measure.label}, ${state.level} level. The table below lists the same values.`);
  if (geo.metadata && geo.metadata.synthetic) {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', '10'); t.setAttribute('y', '20');
    t.setAttribute('font-size', '13'); t.setAttribute('fill', '#7a3b12');
    t.textContent = 'SYNTHETIC SHAPES — not boundaries';
    svg.appendChild(t);
  }
  const interactive = geo.features.length <= 400;

  geo.features.forEach((f) => {
    if (!f.geometry) return;
    const geoid = f.properties.GEOID;
    const v = values?.[geoid];
    const est = v && v.es === 'ok' ? v.e : null;
    const cls = classOf(est);
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', projectPath(f.geometry, project));
    if (cls === null) {
      p.setAttribute('fill', '#f4f1ec');
      p.setAttribute('stroke', '#b9b2a6');
      p.setAttribute('stroke-dasharray', '2 2');
    } else {
      p.setAttribute('fill', SEQUENTIAL[Math.min(cls, SEQUENTIAL.length - 1)]);
      p.setAttribute('stroke', '#ffffff');
    }
    p.setAttribute('stroke-width', '0.6');
    p.dataset.geoid = geoid;
    if (state.selected === geoid) p.dataset.selected = 'true';
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    title.textContent = `${f.properties.name}: ` +
      (est === null ? (v?.er || 'no usable estimate') : fmt(est, measure.unit));
    p.appendChild(title);
    p.addEventListener('click', () => selectArea(geoid));
    if (interactive) {
      p.setAttribute('tabindex', '0');
      p.setAttribute('role', 'button');
      p.setAttribute('aria-label', title.textContent);
      p.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectArea(geoid); }
      });
    }
    svg.appendChild(p);
  });
  host.appendChild(svg);
}

function renderLegend(measure) {
  const el = $('legend');
  const parts = [`<span>${measure.unit === 'percent' ? 'percent' : 'persons'}</span>`];
  for (let i = 0; i <= state.cuts.length; i += 1) {
    parts.push(`<span class="swatch" style="background:${SEQUENTIAL[Math.min(i, SEQUENTIAL.length - 1)]}"></span>`);
    if (i < state.cuts.length) parts.push(`<span>${fmt(state.cuts[i], measure.unit)}</span>`);
  }
  const missing = areasAtLevel(state.dataset)
    .filter((a) => !(state.values?.[a.geoid]?.es === 'ok')).length;
  parts.push('<span class="nodata"></span>');
  parts.push(`<span>no usable estimate (${missing})</span>`);
  parts.push(`<span>· quantile breaks${state.compat?.allowed ? ', shared across both periods' : ''}</span>`);
  el.innerHTML = parts.join(' ');
}

const COLUMNS = [
  { key: 'name', label: 'Area', num: false },
  { key: 'estimate', label: 'Estimate', num: true },
  { key: 'moe', label: 'MOE (90%)', num: true },
  { key: 'cv', label: 'CV %', num: true },
  { key: 'status', label: 'Status', num: false },
];

function rowsForTable(measure) {
  const rows = areasAtLevel(state.dataset).map((a) => {
    const v = state.values?.[a.geoid] || {};
    const vb = state.valuesB?.[a.geoid];
    return {
      geoid: a.geoid,
      name: a.name,
      estimate: v.es === 'ok' ? v.e : null,
      moe: v.ms === 'ok' ? v.m : null,
      cv: v.cv ?? null,
      status: v.es === 'ok' ? (v.rel || 'estimate published') : (v.er || 'unavailable'),
      flags: v.flags || [],
      estimateB: vb && vb.es === 'ok' ? vb.e : null,
      moeB: vb && vb.ms === 'ok' ? vb.m : null,
      v,
    };
  });
  const { key, dir } = state.sort;
  rows.sort((a, b) => {
    const av = a[key]; const bv = b[key];
    // Unavailable values sort to the end in both directions: they are not small.
    if (av === null && bv === null) return a.name.localeCompare(b.name);
    if (av === null) return 1;
    if (bv === null) return -1;
    const c = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
    return dir === 'asc' ? c : -c;
  });
  return rows;
}

function renderTable(measure) {
  const cols = [...COLUMNS];
  if (state.compareId && state.compat?.allowed) {
    cols.splice(2, 0, { key: 'estimateB', label: `Estimate, ${state.datasetB.release.period_label}`, num: true });
  }
  const head = $('table-head');
  head.textContent = '';
  const tr = document.createElement('tr');
  cols.forEach((c) => {
    const th = document.createElement('th');
    th.scope = 'col';
    if (state.sort.key === c.key) th.setAttribute('aria-sort', state.sort.dir === 'asc' ? 'ascending' : 'descending');
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = c.label;
    b.addEventListener('click', () => {
      state.sort = { key: c.key, dir: state.sort.key === c.key && state.sort.dir === 'desc' ? 'asc' : 'desc' };
      renderTable(measure);
    });
    th.appendChild(b);
    tr.appendChild(th);
  });
  head.appendChild(tr);

  const body = $('table-body');
  body.textContent = '';
  const rows = rowsForTable(measure);
  rows.forEach((row) => {
    const tr2 = document.createElement('tr');
    tr2.tabIndex = 0;
    if (state.selected === row.geoid) tr2.dataset.selected = 'true';
    tr2.addEventListener('click', () => selectArea(row.geoid));
    tr2.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectArea(row.geoid); }
    });
    cols.forEach((c) => {
      const td = document.createElement('td');
      if (c.num) td.className = 'num';
      let text;
      if (c.key === 'name') text = row.name;
      else if (c.key === 'status') text = row.status;
      else if (c.key === 'cv') text = row.cv === null ? '—' : `${row.cv.toFixed(1)}`;
      else text = fmt(row[c.key], measure.unit);
      td.textContent = text;
      if ((c.key === 'estimate' || c.key === 'estimateB') && row[c.key] === null) {
        td.classList.add('missing');
        td.textContent = 'no data';
      }
      if (c.key === 'status' && row.flags.length) {
        const s = document.createElement('span');
        s.className = 'flagpill';
        s.textContent = `${row.flags.length} flag${row.flags.length > 1 ? 's' : ''}`;
        td.append(' ', s);
      }
      tr2.appendChild(td);
    });
    body.appendChild(tr2);
  });

  const missing = rows.filter((r) => r.estimate === null).length;
  $('table-title').textContent =
    `${rows.length} ${state.level === 'county' ? 'boroughs' : 'census tracts'}`;
  $('table-note').textContent =
    `Values are ${measure.unit === 'percent' ? 'percentages' : 'person counts'}. ` +
    `${missing} area${missing === 1 ? '' : 's'} have no usable estimate and are listed as ` +
    '"no data", never as zero. Sorting keeps them at the end.';
}

/* -------------------------------------------------------------- drawer */

function selectArea(geoid) {
  state.selected = geoid;
  toggleDrawer(true);
  render();
}

function toggleDrawer(force) {
  const d = $('drawer');
  const open = force === undefined ? d.hidden : force;
  d.hidden = !open;
  document.body.classList.toggle('drawer-open', open);
  $('btn-details').setAttribute('aria-expanded', String(open));
  if (open) renderDrawerBody();
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function renderDrawerBody() {
  const m = measureById(state.measureId);
  if (!m) return;
  const r = state.dataset.release;
  const geoid = state.selected;
  const area = state.dataset.areas.find((a) => a.geoid === geoid);
  const v = geoid ? state.values?.[geoid] : null;
  $('drawer-title').textContent = area ? area.name : 'Measure detail';

  const parts = [];
  if (area && v) {
    parts.push('<h3>This area</h3><dl>');
    parts.push(`<dt>GEOID</dt><dd class="codes">${escapeHtml(geoid)} (string; leading zeros preserved)</dd>`);
    parts.push(`<dt>Level</dt><dd>${escapeHtml(area.level)}</dd>`);
    parts.push(`<dt>Period</dt><dd>${escapeHtml(r.period_label)}</dd>`);
    if (v.es === 'ok') {
      parts.push(`<dt>Estimate</dt><dd>${fmt(v.e, m.unit)} <span class="muted">(${m.unit === 'percent' ? 'percent' : 'persons'})</span></dd>`);
    } else {
      parts.push(`<dt>Estimate</dt><dd class="caveat">Unavailable — ${escapeHtml(v.er || 'no reason recorded')}</dd>`);
    }
    if (v.ms === 'ok') {
      parts.push(`<dt>Margin of error</dt><dd>± ${fmt(v.m, m.unit)} at 90% confidence` +
        (v.mr ? ` <span class="muted">(${escapeHtml(v.mr)})</span>` : '') + '</dd>');
    } else {
      parts.push(`<dt>Margin of error</dt><dd class="caveat">Unavailable — ${escapeHtml(v.mr || 'not published')}. ` +
        'Missing uncertainty is unavailable, not zero.</dd>');
    }
    if (v.cv !== undefined && v.cv !== null) {
      parts.push(`<dt>Relative error</dt><dd>CV ${v.cv.toFixed(1)}% — ${escapeHtml(v.rel)} ` +
        '<span class="muted">(SE = MOE ÷ 1.645)</span></dd>');
    }
    if (m.kind === 'share' && v.n !== undefined) {
      parts.push(`<dt>Numerator</dt><dd>${fmt(v.n, 'persons')} persons</dd>`);
      parts.push(`<dt>Denominator</dt><dd>${fmt(v.d, 'persons')} persons</dd>`);
    }
    parts.push('</dl>');
    if (v.flags && v.flags.length) {
      parts.push('<h3>Source flags</h3><ul>' +
        v.flags.map((f) => `<li>${escapeHtml(f)}</li>`).join('') + '</ul>');
    }
  }

  parts.push('<h3>What this measures</h3>');
  parts.push(`<p>${escapeHtml(m.definition_note)}</p>`);
  parts.push('<dl>');
  parts.push(`<dt>Unit</dt><dd>${escapeHtml(m.unit === 'percent' ? 'percent' : 'persons')}</dd>`);
  parts.push(`<dt>Universe</dt><dd>${escapeHtml(m.universe_published?.[0] || m.universe_note)}</dd>`);
  parts.push(`<dt>Denominator</dt><dd>${m.kind === 'share'
    ? escapeHtml(m.universe_note) : 'not applicable — this is a count'}</dd>`);
  parts.push('</dl>');

  if (m.caveats && m.caveats.length) {
    parts.push('<h3>Read this before quoting the number</h3>');
    m.caveats.forEach((c) => parts.push(`<p class="caveat">${escapeHtml(c)}</p>`));
  }

  parts.push('<h3>Source</h3><dl>');
  parts.push(`<dt>Product</dt><dd>${escapeHtml(r.product_label)}</dd>`);
  parts.push(`<dt>Period</dt><dd>${escapeHtml(r.period_label)}</dd>`);
  parts.push(`<dt>Geography</dt><dd>${escapeHtml(r.geography_vintage)}</dd>`);
  parts.push(`<dt>Citation</dt><dd>${escapeHtml(r.citation)}</dd>`);
  parts.push(`<dt>Data mode</dt><dd>${escapeHtml(state.dataset.data_mode)}</dd>`);
  parts.push('</dl>');

  const join = (state.dataset.join_reports || []).find((j) => j.level === state.level);
  if (join) {
    parts.push('<h3>Geographic join</h3>');
    parts.push(`<p>${join.matched} areas matched at ${escapeHtml(join.boundary_release)}. ` +
      `${join.unmatched_feature_count} boundary features had no observation; ` +
      `${join.unmatched_observation_count} observations had no boundary` +
      (join.unmatched_observation_population !== null && join.unmatched_observation_population !== undefined
        ? ` (total population ${fmt(join.unmatched_observation_population, 'persons')})` : '') + '.</p>');
    if (join.unmatched_observations?.length) {
      parts.push('<p class="codes small">Unmatched: ' +
        escapeHtml(join.unmatched_observations.join(', ')) + '</p>');
    }
  }

  parts.push('<details class="codes-panel"><summary>Research codes and table cells</summary>');
  parts.push('<p class="small muted">Codes are shown here rather than in the main interface. ' +
    'They travel with every export.</p><ul class="codes">');
  (m.cells || []).forEach((c) => {
    parts.push(`<li>${escapeHtml(c.cell)} — ${escapeHtml(c.label)}<br>` +
      `<span class="muted">estimate ${escapeHtml(c.estimate_var)}, MOE ${escapeHtml(c.moe_var || 'none')}, ` +
      `annotations ${escapeHtml(c.estimate_annotation_var || '—')}/${escapeHtml(c.moe_annotation_var || '—')}</span></li>`);
  });
  parts.push('</ul>');
  parts.push(`<p class="small muted">Numerator: ${escapeHtml((m.numerator_cells || []).join(' + '))}` +
    `${m.denominator_cells?.length ? ` · Denominator: ${escapeHtml(m.denominator_cells.join(' + '))}` : ''}</p>`);
  parts.push('</details>');

  $('drawer-body').innerHTML = parts.join('');
}

/* ------------------------------------------------------ projects/export */

async function saveProject(event) {
  event.preventDefault();
  const id = $('project-id').value.trim();
  if (!id) { toast('Give the project a short identifier first.'); return; }
  try {
    const res = await post('/api/projects', {
      project_id: id,
      title: `${measureById(state.measureId).label} — ${state.dataset.release.period_label}`,
      release_id: state.releaseId,
      comparison_release_id: state.compareId || null,
      level: state.level,
      measure_id: state.measureId,
      areas: [],
      classes: 5,
    });
    toast(`Saved ${res.saved} to ${res.path}, pinning ${res.pinned_inputs} input ` +
      'file(s). Reopening it will refuse rather than show different numbers.', 7000);
    $('project-id').value = '';
    renderProjects();
  } catch (e) { toast(`Could not save: ${e.message}`); }
}

async function renderProjects() {
  const list = $('project-list');
  list.textContent = '';
  let items = [];
  try { items = (await api('/api/projects')).projects; } catch { /* listing is optional */ }
  if (!items.length) {
    list.innerHTML = '<li class="muted small">No saved projects yet.</li>';
    return;
  }
  items.forEach((p) => {
    const li = document.createElement('li');
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'button ghost';
    open.textContent = p.project_id;
    open.addEventListener('click', async () => {
      let replay;
      try {
        replay = await api(`/api/project?id=${encodeURIComponent(p.project_id)}`);
      } catch (e) {
        if (e.kind === 'pin_mismatch') {
          showBlock(`Cannot reopen ${p.project_id}`, e.message);
          toast(`${p.project_id} was not reopened: its inputs changed since it was saved.`,
                9000);
        } else {
          toast(`Could not reopen ${p.project_id}: ${e.message}`);
        }
        return;
      }
      const full = replay.project;
      state.releaseId = full.release_id;
      state.compareId = full.comparison_release_id || '';
      state.level = full.level;
      state.measureId = full.measure_id;
      $('release-select').value = state.releaseId;
      $('compare-select').value = state.compareId;
      $('level-select').value = state.level;
      await loadRelease();
      const n = replay.pin?.input_count ?? 0;
      toast(`Reopened ${p.project_id}. All ${n} pinned input${n === 1 ? '' : 's'} were ` +
        'present and unchanged, so this is the result the project was saved from.', 7000);
    });
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'button ghost';
    del.textContent = 'delete';
    del.addEventListener('click', async () => {
      await post('/api/projects/delete', { project_id: p.project_id });
      renderProjects();
    });
    li.append(open, del);
    list.appendChild(li);
  });
}

async function doExport() {
  try {
    const comparing = Boolean(state.compareId && state.compat?.allowed);
    const res = await post('/api/export', {
      release_id: state.releaseId,
      comparison_release_id: comparing ? state.compareId : null,
      measure_id: state.measureId,
      level: state.level,
      // The exported figure covers exactly the areas on screen.
      areas: selectedGeoids(),
      figure_kind: 'map',
      include_figure: true,
    });
    const n = res.selection?.area_count ?? '?';
    toast(`Exported ${res.rows} rows over ${n} ${state.level} areas to ${res.export_dir} ` +
      '(data.csv, provenance.json, figure.svg). The figure covers the same areas.');
  } catch (e) { toast(`Export failed: ${e.message}`); }
}

function renderFooter() {
  const s = state.status;
  const ref = s.annotation_reference || {};
  $('footer-provenance').textContent =
    `Data mode: ${state.dataset.data_mode}. Built ${state.dataset.built_at} from manifest ` +
    `${state.dataset.manifest_id} at code revision ${state.dataset.code_revision}. ` +
    `Transport: ${state.dataset.transport}. Annotation semantics from ${ref.source_url || 'n/a'} ` +
    `(retrieved ${ref.retrieved_at || 'n/a'}). This service holds no credentials and makes no ` +
    'network requests.';
}

boot().catch((e) => {
  document.body.insertAdjacentHTML('afterbegin',
    `<div class="compat blocked" style="margin:16px">Could not start: ${escapeHtml(e.message)}</div>`);
});
