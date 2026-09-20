/* Census Explorer — guided place brief.
 *
 * No framework, no bundler, no CDN. The page talks only to the local service,
 * which holds no credentials and performs no network retrieval.
 *
 * The flow is deliberate: a question first, then the place, then what to show.
 * Every option comes from the service, which offers only measures the built
 * dataset actually carries at that geography. The page never invents one.
 */
'use strict';

const state = {
  status: null,
  releaseId: null,
  catalog: null,        // /api/questions
  questionId: null,
  question: null,
  options: [],          // measure options for this question
  measureId: null,
  level: 'county',
  areas: [],            // chosen GEOIDs ([] means "all at this level")
  benchmarkId: 'none',
  benchmarks: [],
  dataset: null,
  values: null,
  geo: null,
  cuts: [],
  quality: null,
  benchmark: null,
  sort: { key: 'estimate', dir: 'desc' },
  selected: null,
  measureFilter: '',
  loading: false,
};

const SEQUENTIAL = ['#e8eef4', '#bcd0e2', '#89aecb', '#5386ad', '#27618e'];
const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------- transport */

async function api(path) {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  const body = await res.json().catch(() => ({ error: 'the response was not JSON' }));
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
  const body = await res.json().catch(() => ({ error: 'the response was not JSON' }));
  if (!res.ok) {
    const err = new Error(body.error || `request failed (${res.status})`);
    err.kind = body.kind;
    throw err;
  }
  return body;
}

function toast(message, ms = 5200) {
  const el = $('toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function fmt(value, unit) {
  if (value === null || value === undefined) return '—';
  if (unit === 'percent') return `${value.toFixed(1)}%`;
  return Math.round(value).toLocaleString('en-US');
}

function currentOption() {
  return state.options.find((o) => o.measure_id === state.measureId) || null;
}

function areaParam() {
  return state.areas.length ? `&areas=${encodeURIComponent(state.areas.join(','))}` : '';
}

function selectionQuery() {
  return `release=${encodeURIComponent(state.releaseId)}` +
    `&measure=${encodeURIComponent(state.measureId)}` +
    `&level=${state.level}${areaParam()}`;
}

/* ------------------------------------------------------------------ boot */

async function boot() {
  state.status = await api('/api/status');
  if (!state.status.releases.length) {
    $('startup').innerHTML = '<p class="blocked-note"><strong>No dataset is built yet.</strong>' +
      'Run <code>python -m census_explorer.cli fetch all</code> then ' +
      '<code>build</code>, or <code>fixtures build</code> to try the app with ' +
      'synthetic data.</p>';
    return;
  }
  if (state.status.data_mode === 'fixture') {
    const b = $('mode-banner');
    b.hidden = false;
    b.textContent = 'FIXTURE MODE — every value shown is synthetic test data and the ' +
      'shapes are generated rectangles, not boundaries. Nothing here is a census finding.';
  }
  state.releaseId = state.status.releases.some((r) => r.release_id === state.status.default_release)
    ? state.status.default_release : state.status.releases[0].release_id;

  state.catalog = await api(`/api/questions?release=${encodeURIComponent(state.releaseId)}`);
  $('period-chip').textContent = `Reference period: ${state.catalog.period_label}`;
  state.dataset = await api(`/api/dataset?release=${encodeURIComponent(state.releaseId)}`);

  renderQuestions();
  wireControls();
  $('startup').hidden = true;
  $('workspace').hidden = false;

  const first = state.catalog.questions.find((q) => q.supported);
  if (first) await chooseQuestion(first.question_id);
  renderProjects();
  renderFooter();
}

function wireControls() {
  $('measure-search').addEventListener('input', (e) => {
    state.measureFilter = e.target.value.trim().toLowerCase();
    renderMeasureOptions();
  });
  $('measure-select').addEventListener('change', async (e) => {
    state.measureId = e.target.value;
    await refresh();
  });
  $('benchmark-select').addEventListener('change', async (e) => {
    state.benchmarkId = e.target.value;
    await refresh();
  });
  $('btn-quality').addEventListener('click', () => toggleDrawer());
  $('drawer-close').addEventListener('click', () => toggleDrawer(false));
  $('btn-brief').addEventListener('click', openBrief);
  $('btn-export').addEventListener('click', doExport);
  $('save-form').addEventListener('submit', saveProject);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('drawer').hidden) toggleDrawer(false);
  });
}

/* --------------------------------------------------------- step 1: question */

function renderQuestions() {
  const host = $('question-list');
  host.textContent = '';
  state.catalog.questions.forEach((q) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'question';
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(q.question_id === state.questionId));
    b.disabled = !q.supported;
    b.innerHTML = `<span class="q-title">${esc(q.title)}</span>` +
      `<span class="q-sub">${esc(q.supported ? q.subtitle : q.unsupported_reason)}</span>`;
    b.addEventListener('click', () => chooseQuestion(q.question_id));
    host.appendChild(b);
  });
}

async function chooseQuestion(questionId) {
  const q = state.catalog.questions.find((x) => x.question_id === questionId);
  if (!q || !q.supported) return;
  state.questionId = questionId;
  state.question = q;
  state.options = q.measures;
  state.level = q.level;
  state.measureId = q.measures[0]?.measure_id || null;
  state.measureFilter = '';
  state.selected = null;

  // Default places per question, so the user sees an answer immediately.
  const counties = state.catalog.places.county;
  if (q.place_mode === 'single') state.areas = [counties[0].geoid];
  else state.areas = [];

  $('measure-search').value = '';
  $('place-prompt').textContent = q.place_prompt;
  $('measure-prompt').textContent = q.measure_prompt;
  $('benchmark-prompt').textContent = q.benchmark_prompt || 'Compare against';
  renderQuestions();
  renderPlaceControls();
  renderMeasureOptions();
  await loadBenchmarks();
  await refresh();
}

/* ------------------------------------------------------------ step 2: place */

function renderPlaceControls() {
  const host = $('place-controls');
  host.textContent = '';
  const q = state.question;
  const counties = state.catalog.places.county;

  if (q.place_mode === 'all') {
    const p = document.createElement('p');
    p.className = 'place-all';
    p.textContent = `All ${state.catalog.places.tract_count.toLocaleString('en-US')} ` +
      'census tracts in the five boroughs. Census tracts are statistical areas ' +
      'published by the Census Bureau, not neighbourhoods; this build has no ' +
      'documented neighbourhood boundaries.';
    host.appendChild(p);
    return;
  }

  if (q.place_mode === 'single') {
    const label = document.createElement('label');
    label.className = 'field';
    label.innerHTML = '<span class="visually-hidden">Place</span>';
    const sel = document.createElement('select');
    counties.forEach((c) => sel.add(new Option(c.name, c.geoid)));
    sel.value = state.areas[0] || counties[0].geoid;
    sel.addEventListener('change', async () => {
      state.areas = [sel.value];
      await loadBenchmarks();
      await refresh();
    });
    label.appendChild(sel);
    host.appendChild(label);
    return;
  }

  const box = document.createElement('div');
  box.className = 'place-checks';
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', 'Places to compare');
  counties.forEach((c) => {
    const id = `place-${c.geoid}`;
    const label = document.createElement('label');
    label.setAttribute('for', id);
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.id = id;
    cb.value = c.geoid;
    cb.checked = state.areas.length === 0 || state.areas.includes(c.geoid);
    cb.addEventListener('change', async () => {
      const checked = Array.from(box.querySelectorAll('input:checked')).map((x) => x.value);
      if (!checked.length) { cb.checked = true; toast('Keep at least one place selected.'); return; }
      state.areas = checked.length === counties.length ? [] : checked;
      await loadBenchmarks();
      await refresh();
    });
    label.append(cb, document.createTextNode(' ' + c.name));
    box.appendChild(label);
  });
  host.appendChild(box);
}

/* ---------------------------------------------------------- step 3: measure */

function renderMeasureOptions() {
  const host = $('measure-select');
  host.textContent = '';
  const needle = state.measureFilter;
  const matches = state.options.filter((o) => !needle ||
    `${o.label} ${o.counts_what} ${o.out_of}`.toLowerCase().includes(needle));
  if (!matches.length) {
    host.innerHTML = '<p class="muted small" style="padding:8px">' +
      'No measure matches that filter. Clear it to see all ' +
      `${state.options.length} options for this question.</p>`;
    return;
  }
  if (!matches.some((o) => o.measure_id === state.measureId)) {
    state.measureId = matches[0].measure_id;
  }
  matches.forEach((o) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'measure-option';
    b.setAttribute('role', 'option');
    b.setAttribute('aria-selected', String(o.measure_id === state.measureId));
    // Labels wrap: a native select clips them, and a clipped measure name is
    // exactly the thing a reader must not have to guess at.
    b.innerHTML = `<span class="m-label">${esc(o.label)}</span>` +
      `<span class="m-sub">${esc(o.unit === 'percent'
        ? 'share, out of ' + o.out_of : 'number of people')}</span>`;
    b.addEventListener('click', async () => {
      state.measureId = o.measure_id;
      renderMeasureOptions();
      await refresh();
    });
    host.appendChild(b);
  });
  // Scroll the container rather than calling scrollIntoView: that also moves
  // the browser's sequential-focus starting point, which sent the first Tab
  // into this list instead of the skip link.
  const active = host.querySelector('[aria-selected="true"]');
  if (active && needle === '') {
    const top = active.offsetTop;
    const bottom = top + active.offsetHeight;
    if (top < host.scrollTop) host.scrollTop = top;
    else if (bottom > host.scrollTop + host.clientHeight) {
      host.scrollTop = bottom - host.clientHeight;
    }
  }
}

async function loadBenchmarks() {
  const res = await api(`/api/benchmarks?release=${encodeURIComponent(state.releaseId)}` +
    `&level=${state.level}${areaParam()}`);
  state.benchmarks = res.benchmarks;
  const sel = $('benchmark-select');
  sel.textContent = '';
  state.benchmarks.forEach((b) => sel.add(new Option(b.label, b.benchmark_id)));
  if (!state.benchmarks.some((b) => b.benchmark_id === state.benchmarkId)) {
    state.benchmarkId = 'none';
  }
  sel.value = state.benchmarkId;
  const chosen = state.benchmarks.find((b) => b.benchmark_id === state.benchmarkId);
  $('benchmark-note').textContent = chosen ? chosen.description : '';
}

/* -------------------------------------------------------------- data + view */

function setLoading(on) {
  state.loading = on;
  ['btn-brief', 'btn-export', 'btn-quality'].forEach((id) => { $(id).disabled = on; });
  if (on) $('map').innerHTML = '<p class="spinner">Loading…</p>';
}

async function refresh() {
  if (!state.measureId) return;
  setLoading(true);
  $('blocked').hidden = true;
  try {
    const [values, geo, quality] = await Promise.all([
      api(`/api/values?release=${encodeURIComponent(state.releaseId)}` +
          `&measure=${encodeURIComponent(state.measureId)}`),
      api(`/api/geography?release=${encodeURIComponent(state.releaseId)}&level=${state.level}`),
      api(`/api/quality?${selectionQuery()}`),
    ]);
    state.values = values.values;
    state.geo = geo;
    state.quality = quality.quality;
    state.selectionJson = quality.selection;
    state.benchmark = null;
    if (state.benchmarkId && state.benchmarkId !== 'none') {
      state.benchmark = await api(`/api/benchmark?${selectionQuery()}` +
        `&benchmark=${encodeURIComponent(state.benchmarkId)}`);
    }
    computeCuts();
    render();
  } catch (e) {
    showBlocked('This selection could not be shown', [e.message]);
    $('map').innerHTML = '<p class="empty">Nothing to draw for this selection.</p>';
  } finally {
    setLoading(false);
  }
}

function selectedGeoids() {
  return state.selectionJson ? state.selectionJson.areas : state.areas;
}

function usableValues() {
  return selectedGeoids()
    .map((g) => state.values?.[g])
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

function computeCuts() { state.cuts = quantileCuts(usableValues(), 5); }

function classOf(value) {
  if (value === null || value === undefined) return null;
  for (let i = 0; i < state.cuts.length; i += 1) if (value < state.cuts[i]) return i;
  return state.cuts.length;
}

function showBlocked(title, lines) {
  const el = $('blocked');
  el.hidden = false;
  el.innerHTML = `<strong>${esc(title)}</strong><ul>` +
    lines.map((l) => `<li>${esc(l)}</li>`).join('') + '</ul>';
}

function render() {
  const o = currentOption();
  if (!o) return;
  renderSummary(o);
  $('measure-title').textContent = o.label;
  $('map-caption').textContent =
    `${o.label} — ${state.catalog.period_label}, ` +
    `${state.level === 'county' ? 'boroughs' : 'census tracts'}. ` +
    (o.unit === 'percent' ? 'Shaded by percentage.' : 'Shaded by number of people.');
  drawMap();
  renderLegend(o);
  renderTable(o);
  renderSourceDetails(o);
  if (!$('drawer').hidden) renderDrawer();
}

function renderSummary(o) {
  const q = state.question;
  const places = describePlaces();
  const rows = [
    ['Question', q.title],
    ['Places', places],
    ['Reference period', `${state.catalog.period_label} — a five-year period estimate, not a single year`],
    ['What is counted', o.counts_what],
    [o.unit === 'percent' ? 'Out of' : 'Units',
      o.unit === 'percent' ? o.out_of : 'people (a number, not a share)'],
  ];
  if (state.benchmark && state.benchmark.available) {
    rows.push(['Reference', `${state.benchmark.label} — ${state.benchmark.basis}`]);
  } else if (state.benchmark && !state.benchmark.available) {
    rows.push(['Reference', `unavailable — ${state.benchmark.unavailable_reason}`]);
  }
  $('selection-summary').innerHTML =
    `<p class="headline">${esc(o.label)}</p><dl>` +
    rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('') + '</dl>';
}

function describePlaces() {
  const counties = state.catalog.places.county;
  const names = Object.fromEntries(counties.map((c) => [c.geoid, c.name]));
  if (state.level === 'tract') {
    return `${(state.selectionJson?.area_count ?? 0).toLocaleString('en-US')} census ` +
      'tracts in New York City (statistical areas, not neighbourhoods)';
  }
  const chosen = state.areas.length ? state.areas : counties.map((c) => c.geoid);
  return chosen.map((g) => names[g] || g).join(', ');
}

/* ------------------------------------------------------------------- map */

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

function drawMap() {
  const host = $('map');
  host.textContent = '';
  const visible = new Set(selectedGeoids());
  const features = (state.geo?.features || []).filter((f) => visible.has(f.properties.GEOID));
  if (!features.length) {
    host.innerHTML = '<p class="empty">No boundary layer is available for these ' +
      'areas at this geography vintage. The table below still carries every value.</p>';
    return;
  }
  let minx = Infinity; let miny = Infinity; let maxx = -Infinity; let maxy = -Infinity;
  const walk = (n) => {
    if (typeof n[0] === 'number') {
      minx = Math.min(minx, n[0]); maxx = Math.max(maxx, n[0]);
      miny = Math.min(miny, n[1]); maxy = Math.max(maxy, n[1]);
    } else n.forEach(walk);
  };
  features.forEach((f) => f.geometry && walk(f.geometry.coordinates));

  const W = 700; const H = 460; const pad = 10;
  const sx = Math.cos(((miny + maxy) / 2) * Math.PI / 180);
  const scale = Math.min((W - 2 * pad) / ((maxx - minx) * sx), (H - 2 * pad) / (maxy - miny));
  const ox = pad + ((W - 2 * pad) - (maxx - minx) * sx * scale) / 2;
  const oy = pad + ((H - 2 * pad) - (maxy - miny) * scale) / 2;
  const project = (x, y) => [ox + (x - minx) * sx * scale, oy + (maxy - y) * scale];

  const o = currentOption();
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label',
    `${o.label}, ${state.level === 'county' ? 'boroughs' : 'census tracts'}. ` +
    'The table below lists the same values.');
  if (state.geo.metadata && state.geo.metadata.synthetic) {
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    t.setAttribute('x', '10'); t.setAttribute('y', '20');
    t.setAttribute('font-size', '13'); t.setAttribute('fill', '#7a3b12');
    t.textContent = 'SYNTHETIC SHAPES — not boundaries';
    svg.appendChild(t);
  }
  const interactive = features.length <= 400;

  features.forEach((f) => {
    if (!f.geometry) return;
    const geoid = f.properties.GEOID;
    const v = state.values?.[geoid];
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
      (est === null ? (v ? plainReason(v, 'e') : 'no usable estimate') : fmt(est, o.unit));
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

function renderLegend(o) {
  const el = $('legend');
  const parts = [`<span>${o.unit === 'percent' ? 'percent of the denominator' : 'people'}</span>`];
  for (let i = 0; i <= state.cuts.length; i += 1) {
    parts.push(`<span class="swatch" style="background:${SEQUENTIAL[Math.min(i, SEQUENTIAL.length - 1)]}"></span>`);
    if (i < state.cuts.length) parts.push(`<span>${fmt(state.cuts[i], o.unit)}</span>`);
  }
  const missing = selectedGeoids().filter((g) => !(state.values?.[g]?.es === 'ok')).length;
  parts.push('<span class="nodata"></span>');
  parts.push(`<span>no usable estimate (${missing})</span>`);
  el.innerHTML = parts.join(' ');
}

/* ----------------------------------------------------------------- table */

const COLUMNS = [
  { key: 'name', label: 'Area', num: false },
  { key: 'estimate', label: 'Estimate', num: true },
  { key: 'moe', label: 'Margin of error (90%)', num: true },
  { key: 'quality', label: 'Reliability', num: false },
];

function plainReason(v, which) {
  // The stored reason names the table cell; that belongs in source details.
  const raw = (which === 'm' ? v.mr : v.er) || '';
  if (!raw) return 'unavailable';
  const body = /^[A-Z0-9]+_\d{3}: /.test(raw) ? raw.split(': ').slice(1).join(': ') : raw;
  const lowered = body.charAt(0).toLowerCase() + body.slice(1);
  if (lowered.includes('insufficient number of sample cases')) {
    return 'too few sample cases here for the Census Bureau to publish a value';
  }
  if (lowered.includes('insufficient number of sample observations')) {
    return 'too few sample observations here to compute a value';
  }
  if (lowered.includes('undefined, not zero')) {
    return 'the denominator is zero here, so a share is undefined, not zero';
  }
  if (lowered.includes('controlled')) {
    return 'controlled to an independent population estimate, so it carries no sampling error';
  }
  return lowered;
}

function isControlled(v) {
  // The explicit flag recorded when the value was computed. Never inferred
  // from source-flag text: flags are pooled across numerator and denominator,
  // so one cell's flag says nothing about the result's uncertainty.
  return v.ctl === true;
}

function fmtMoe(value, unit) {
  // A margin of error on a percentage is a span of percentage points; writing
  // it as a percentage invites reading it as a share of the estimate.
  if (value === null || value === undefined) return '—';
  return unit === 'percent' ? `± ${value.toFixed(1)} points` : `± ${fmt(value, unit)}`;
}

function reliabilityWords(v, unit) {
  // Says something the margin-of-error column does not already say.
  if (v.es !== 'ok') return plainReason(v, 'e');
  if (isControlled(v)) {
    return 'controlled to an independent population estimate, so it carries no sampling error';
  }
  if (v.ms !== 'ok') return `margin of error unavailable: ${plainReason(v, 'm')}`;
  if (v.rel) return v.cv !== undefined && v.cv !== null
    ? `${v.rel} (CV ${v.cv.toFixed(0)}%)` : v.rel;
  if (unit === 'percent' && v.m !== null && v.m !== undefined && v.e) {
    const ratio = Math.abs(v.m) / Math.abs(v.e) * 100;
    const band = ratio < 10 ? 'narrow' : (ratio < 30 ? 'moderate' : 'wide — read as indicative');
    return `${band}: the margin of error is ${ratio.toFixed(0)}% of the estimate`;
  }
  return 'estimate published';
}

function rowsForTable() {
  const o = currentOption();
  const rows = selectedGeoids().map((geoid) => {
    const v = state.values?.[geoid] || {};
    const area = state.dataset.areas.find((a) => a.geoid === geoid);
    return {
      geoid,
      name: area ? area.name : geoid,
      estimate: v.es === 'ok' ? v.e : null,
      moe: v.ms === 'ok' ? v.m : null,
      controlled: isControlled(v),
      quality: reliabilityWords(v, o.unit),
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

function renderTable(o) {
  const head = $('table-head');
  head.textContent = '';
  const tr = document.createElement('tr');
  COLUMNS.forEach((c) => {
    const th = document.createElement('th');
    th.scope = 'col';
    if (state.sort.key === c.key) {
      th.setAttribute('aria-sort', state.sort.dir === 'asc' ? 'ascending' : 'descending');
    }
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = c.label;
    b.addEventListener('click', () => {
      state.sort = { key: c.key, dir: state.sort.key === c.key && state.sort.dir === 'desc' ? 'asc' : 'desc' };
      renderTable(o);
    });
    th.appendChild(b);
    tr.appendChild(th);
  });
  head.appendChild(tr);

  const body = $('table-body');
  body.textContent = '';
  if (state.benchmark) {
    body.appendChild(benchmarkRow(o));
  }
  const rows = rowsForTable();
  const shown = rows.slice(0, 400);
  shown.forEach((row) => {
    const tr2 = document.createElement('tr');
    tr2.tabIndex = 0;
    if (state.selected === row.geoid) tr2.dataset.selected = 'true';
    tr2.addEventListener('click', () => selectArea(row.geoid));
    tr2.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectArea(row.geoid); }
    });
    COLUMNS.forEach((c) => {
      const td = document.createElement('td');
      if (c.num) td.className = 'num';
      if (c.key === 'name' || c.key === 'quality') td.textContent = row[c.key];
      else if (c.key === 'moe' && row.controlled) td.textContent = 'none';
      else if (row[c.key] === null) {
        td.classList.add('missing');
        td.textContent = c.key === 'estimate' ? 'no data' : '—';
      } else {
        td.textContent = c.key === 'moe' ? fmtMoe(row[c.key], o.unit)
          : fmt(row[c.key], o.unit);
      }
      tr2.appendChild(td);
    });
    body.appendChild(tr2);
  });

  const missing = rows.filter((r) => r.estimate === null).length;
  $('table-title').textContent =
    `${rows.length.toLocaleString('en-US')} ${state.level === 'county' ? 'boroughs' : 'census tracts'}`;
  $('table-note').textContent =
    `Values are ${o.unit === 'percent' ? 'percentages of ' + o.out_of : 'counts of people'}. ` +
    `${missing} area${missing === 1 ? '' : 's'} have no usable estimate and read "no data", ` +
    'never zero; sorting keeps them at the end.' +
    (rows.length > shown.length ? ` Showing the first ${shown.length}; the export contains all.` : '');
}

function benchmarkRow(o) {
  const b = state.benchmark;
  const tr = document.createElement('tr');
  tr.className = 'benchmark-row';
  if (!b.available) {
    // A reference that was asked for and could not be built is information.
    [b.label, 'not available', '—', b.unavailable_reason || 'no reason recorded']
      .forEach((text, i) => {
        const td = document.createElement('td');
        if (i === 1 || i === 2) td.className = 'num';
        if (i === 1) td.classList.add('missing');
        td.textContent = text;
        tr.appendChild(td);
      });
    return tr;
  }
  let moeText = '—';
  let note = 'reference value; margin of error unavailable';
  if (b.controlled) {
    moeText = 'none';
    note = 'reference value; controlled total, so no sampling error';
  } else if (b.moe_status === 'ok') {
    moeText = fmtMoe(b.moe, o.unit);
    note = 'reference value';
  }
  [b.label, b.estimate === null ? 'no data' : fmt(b.estimate, o.unit), moeText, note]
    .forEach((text, i) => {
      const td = document.createElement('td');
      if (i === 1 || i === 2) td.className = 'num';
      td.textContent = text;
      tr.appendChild(td);
    });
  return tr;
}

function renderSourceDetails(o) {
  const m = state.dataset.measures.find((x) => x.measure_id === state.measureId);
  if (!m) return;
  const cells = (m.cells || []).map((c) =>
    `<li>${esc(c.cell)} — ${esc(c.label)}<br><span class="muted">estimate ` +
    `${esc(c.estimate_var)}, margin of error ${esc(c.moe_var || 'none published')}` +
    `</span></li>`).join('');
  $('source-details').innerHTML =
    `<dl><dt>Published universe</dt><dd>${esc(m.universe_published?.[0] || m.universe_note)}</dd>` +
    `<dt>Definition</dt><dd>${esc(m.definition_note)}</dd>` +
    `<dt>Tables</dt><dd>${esc((m.tables || []).join(', '))}</dd>` +
    `<dt>Numerator cells</dt><dd class="codes">${esc((m.numerator_cells || []).join(' + '))}</dd>` +
    `<dt>Denominator cells</dt><dd class="codes">${esc((m.denominator_cells || []).join(' + ') || 'not applicable')}</dd>` +
    `<dt>Source</dt><dd>${esc(state.dataset.release.citation)}</dd></dl>` +
    `<ul class="codes">${cells}</ul>`;
}

/* ---------------------------------------------------------------- drawer */

function selectArea(geoid) {
  state.selected = geoid;
  toggleDrawer(true);
  drawMap();
  renderTable(currentOption());
}

function toggleDrawer(force) {
  const d = $('drawer');
  const open = force === undefined ? d.hidden : force;
  d.hidden = !open;
  document.body.classList.toggle('drawer-open', open);
  $('btn-quality').setAttribute('aria-expanded', String(open));
  if (open) renderDrawer();
}

function renderDrawer() {
  const o = currentOption();
  const q = state.quality;
  if (!o || !q) return;
  const parts = [];

  if (state.selected) {
    const v = state.values?.[state.selected] || {};
    const area = state.dataset.areas.find((a) => a.geoid === state.selected);
    parts.push(`<h3>${esc(area ? area.name : state.selected)}</h3><dl>`);
    parts.push(`<dt>Estimate</dt><dd>${v.es === 'ok'
      ? esc(fmt(v.e, o.unit)) : `<span class="caveat">unavailable — ${esc(v.er || 'no reason recorded')}</span>`}</dd>`);
    parts.push(`<dt>Margin of error</dt><dd>${v.ms === 'ok'
      ? (isControlled(v)
        ? 'none — controlled to an independent population estimate'
        : `${esc(fmtMoe(v.m, o.unit))} at 90% confidence`)
      : `<span class="caveat">unavailable — ${esc(plainReason(v, 'm'))}. Missing uncertainty is unavailable, not zero.</span>`}</dd>`);
    if (v.cv !== undefined && v.cv !== null) {
      parts.push(`<dt>Relative error</dt><dd>${v.cv.toFixed(1)}% — ${esc(v.rel)}</dd>`);
    }
    if (v.n !== undefined) {
      parts.push(`<dt>Counted</dt><dd>${esc(fmt(v.n, 'persons'))} people</dd>`);
      parts.push(`<dt>Out of</dt><dd>${esc(fmt(v.d, 'persons'))} people</dd>`);
    }
    parts.push(`<dt>Area code</dt><dd>${esc(state.selected)}</dd>`);
    parts.push('</dl>');
    if (v.flags && v.flags.length) {
      parts.push('<h3>Source flags</h3><ul>' +
        v.flags.map((f) => `<li>${esc(f)}</li>`).join('') + '</ul>');
    }
  }

  [['Uncertainty', q.uncertainty], ['Comparison', q.comparison], ['Period', q.freshness]]
    .forEach(([title, panel]) => {
      parts.push(`<h3>${esc(title)}</h3>`);
      parts.push(`<p class="state ${esc(panel.class)}">${esc(panel.state)}</p>`);
      panel.lines.forEach((line) => parts.push(`<p>${esc(line)}</p>`));
    });

  parts.push('<h3>What this does not say</h3>');
  state.question.not_answered.forEach((x) => parts.push(`<p class="caveat">${esc(x)}</p>`));
  const m = state.dataset.measures.find((x) => x.measure_id === state.measureId);
  (m?.caveats || []).forEach((x) => parts.push(`<p class="caveat">${esc(x)}</p>`));

  $('drawer-body').innerHTML = parts.join('');
}

/* --------------------------------------------------------- brief + export */

function briefQuery() {
  return `/api/brief?${selectionQuery()}` +
    `&question=${encodeURIComponent(state.questionId)}` +
    `&benchmark=${encodeURIComponent(state.benchmarkId)}`;
}

function openBrief() {
  window.open(briefQuery(), '_blank', 'noopener');
  toast('The brief opened in a new tab. Use your browser’s print dialog to save it as PDF.');
}

async function doExport() {
  try {
    const res = await post('/api/export', {
      release_id: state.releaseId,
      measure_id: state.measureId,
      level: state.level,
      areas: selectedGeoids(),
      question_id: state.questionId,
      benchmark_id: state.benchmarkId,
      figure_kind: state.level === 'tract' ? 'map' : 'chart',
      include_figure: true,
    });
    toast(`Exported ${res.rows} rows over ${res.selection.area_count} areas to ` +
      `${res.export_dir}: brief.html, data.csv, provenance.json, figure.svg.`);
  } catch (e) { toast(`Export failed: ${e.message}`); }
}

/* -------------------------------------------------------------- projects */

async function saveProject(event) {
  event.preventDefault();
  const id = $('project-id').value.trim();
  if (!id) { toast('Give the brief a short name first.'); return; }
  try {
    const res = await post('/api/projects', {
      project_id: id,
      title: `${currentOption().label} — ${state.catalog.period_label}`,
      release_id: state.releaseId,
      measure_id: state.measureId,
      level: state.level,
      areas: selectedGeoids(),
      question_id: state.questionId,
      benchmark_id: state.benchmarkId,
    });
    toast(`Saved "${res.saved}", pinning ${res.pinned_inputs} input files. Reopening ` +
      'checks them and refuses rather than showing different numbers.');
    $('project-id').value = '';
    renderProjects();
  } catch (e) { toast(`Could not save: ${e.message}`); }
}

async function renderProjects() {
  const list = $('project-list');
  list.textContent = '';
  let items = [];
  try { items = (await api('/api/projects')).projects; } catch { /* optional */ }
  if (!items.length) {
    list.innerHTML = '<li class="muted small">Nothing saved yet.</li>';
    return;
  }
  items.forEach((p) => {
    const li = document.createElement('li');
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'button ghost p-name';
    open.innerHTML = `${esc(p.project_id)}<span class="p-q">${esc(p.question_id || 'saved view')}</span>`;
    open.addEventListener('click', () => reopen(p.project_id));
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'button ghost';
    del.textContent = 'delete';
    del.setAttribute('aria-label', `Delete ${p.project_id}`);
    del.addEventListener('click', async () => {
      await post('/api/projects/delete', { project_id: p.project_id });
      renderProjects();
    });
    li.append(open, del);
    list.appendChild(li);
  });
}

async function reopen(projectId) {
  let replay;
  try {
    replay = await api(`/api/project?id=${encodeURIComponent(projectId)}`);
  } catch (e) {
    if (e.kind === 'pin_mismatch') {
      showBlocked(`"${projectId}" was not reopened`, [
        e.message,
        'Nothing was substituted and nothing was re-fetched.',
      ]);
      toast(`"${projectId}" was not reopened: its inputs changed since it was saved.`, 9000);
    } else {
      toast(`Could not reopen ${projectId}: ${e.message}`);
    }
    return;
  }
  const p = replay.project;
  const brief = replay.brief || {};
  state.releaseId = p.release_id;
  state.level = p.level;
  state.measureId = p.measure_id;
  state.areas = p.areas || [];
  state.benchmarkId = brief.benchmark_id || 'none';
  if (brief.question_id) {
    state.questionId = brief.question_id;
    state.question = state.catalog.questions.find((q) => q.question_id === brief.question_id);
    state.options = state.question ? state.question.measures : state.options;
  }
  renderQuestions();
  renderPlaceControls();
  renderMeasureOptions();
  await loadBenchmarks();
  await refresh();
  const n = replay.pin?.input_count ?? 0;
  toast(`Reopened "${projectId}". All ${n} pinned input files were present and ` +
    'unchanged, so this is the brief that was saved.', 7000);
}

function renderFooter() {
  const ref = state.status.annotation_reference || {};
  $('footer-provenance').textContent =
    `Data mode: ${state.dataset.data_mode}. Built ${state.dataset.built_at} from manifest ` +
    `${state.dataset.manifest_id} at code revision ${state.dataset.code_revision}. ` +
    `Annotation semantics from ${ref.source_url || 'n/a'} (retrieved ${ref.retrieved_at || 'n/a'}). ` +
    'This service holds no credentials and makes no network requests.';
}

boot().catch((e) => {
  $('startup').innerHTML =
    `<p class="blocked-note"><strong>Could not start</strong>${esc(e.message)}</p>`;
});
