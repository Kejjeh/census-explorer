/* Census Explorer — the explorer interface.
 *
 * No framework, no bundler, no CDN. The page talks only to the local service,
 * which holds no credentials and performs no network retrieval. Every option
 * it offers comes from the built dataset: the page never invents a measure, a
 * place, a denominator or a margin of error.
 */
'use strict';

const state = {
  status: null,
  releaseId: null,
  dataset: null,
  catalog: null,          // /api/catalog, both levels
  level: 'county',
  measureId: null,
  areas: [],              // [] means every area at this level
  values: null,
  geo: null,
  quality: null,
  questionId: null,       // resolved by the service from the selection
  selectionJson: null,
  benchmarkId: 'none',
  benchmarks: [],
  benchmark: null,
  cuts: [],
  sort: { key: 'estimate', dir: 'desc' },
  pick: null,             // the inspected area
  compare: [],            // up to two geoids, deliberately chosen
  hover: null,
  measureFilter: '',
  unitFilter: 'all',
  placeQuery: '',
  view: { k: 1, x: 0, y: 0 },
  loading: false,
  //: True only while the values, geometry, quality and reference on screen all
  //: came from one completed load of the selection now shown. Saving and
  //: exporting are refused until then, because both write the selection to
  //: disk and neither may describe a view that is half of one load and half of
  //: another.
  ready: false,
  breaks: { cuts: [], min: null, max: null, classes: 0 },
};

// Overlapping loads: only the newest may change anything. Separate gates,
// because a benchmark list and a reopened project are not the same request
// and must not invalidate each other's results.
const dataGate = createLoadGate();
const benchGate = createLoadGate();
const projectGate = createLoadGate();

/**
 * Set when this page was produced by `census_explorer.cli site build` and is
 * being served as files, with no Python behind it. The transport below then
 * answers out of the build's own output instead of over HTTP to the service,
 * and the features that genuinely need the service are turned off with a
 * reason rather than left to fail when clicked.
 */
const STATIC = (typeof window !== 'undefined' && window.CENSUS_EXPLORER_STATIC)
  ? createStaticBackend(window.CENSUS_EXPLORER_STATIC) : null;

const RAMP = ['#eaf1f7', '#c3d9ea', '#8fb8d6', '#5691bd', '#1f6199'];

/** The shade for class `i`. A map with one class uses a middle shade rather
 *  than the lightest, which would read as a low value on a scale that has no
 *  low and no high. */
function rampColour(i) {
  if (state.breaks.classes === 1) return RAMP[2];
  return RAMP[Math.min(i, RAMP.length - 1)];
}
const MAX_TABLE_ROWS = 400;
//: Pointer travel, in map units, past which a gesture is a pan rather than a
//: click. Small enough that a deliberate click on a tract still selects it.
const DRAG_SLOP = 4;
const $ = (id) => document.getElementById(id);

/* ------------------------------------------------------------- transport */

async function api(path) {
  if (STATIC) {
    const cut = path.indexOf('?');
    return STATIC.get(cut === -1 ? path : path.slice(0, cut),
                      new URLSearchParams(cut === -1 ? '' : path.slice(cut + 1)));
  }
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
  if (STATIC) {
    const err = new Error(`${path} needs the local Python service, which a ` +
      'published site does not run.');
    err.kind = 'static_unsupported';
    throw err;
  }
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

/* ------------------------------------------------------------- utilities */

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

function fmtMoe(value, unit) {
  // A margin of error on a percentage is a span of percentage points; writing
  // it as a percentage invites reading it as a share of the estimate.
  if (value === null || value === undefined) return '—';
  return unit === 'percent' ? `± ${value.toFixed(1)} points` : `± ${fmt(value, unit)}`;
}

function isControlled(v) {
  // The explicit flag recorded when the value was computed. Never inferred
  // from source-flag text: flags are pooled across numerator and denominator,
  // so one cell's flag says nothing about the result's uncertainty.
  return v && v.ctl === true;
}

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

function reliabilityWords(v, unit) {
  if (!v || v.es !== 'ok') return v ? plainReason(v, 'e') : 'no value built for this area';
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

function levelInfo(level) { return state.catalog.levels[level || state.level]; }

function measures() {
  const groups = levelInfo().groups;
  return groups.flatMap((g) => g.measures);
}

function currentMeasure() {
  return measures().find((m) => m.measure_id === state.measureId) || null;
}

function areaName(geoid) {
  const a = state.dataset.areas.find((x) => x.geoid === geoid);
  return a ? a.name : geoid;
}

function levelNoun(n) {
  const word = state.level === 'county' ? 'borough' : 'census tract';
  return n === 1 ? word : `${word}s`;
}

function scopedGeoids() {
  if (state.selectionJson) return state.selectionJson.areas;
  if (state.areas.length) return state.areas;
  return state.dataset.areas.filter((a) => a.level === state.level).map((a) => a.geoid);
}

/**
 * A frozen copy of what is selected right now.
 *
 * Every request in a load is built from this snapshot rather than from the
 * live state, so a control changed while the load is in flight cannot make one
 * request describe a different selection from the next.
 */
function currentRequest() {
  return {
    releaseId: state.releaseId,
    measureId: state.measureId,
    level: state.level,
    areas: [...state.areas],
    benchmarkId: state.benchmarkId,
  };
}

function areaParamFor(req) {
  return req.areas.length ? `&areas=${encodeURIComponent(req.areas.join(','))}` : '';
}

function selectionQueryFor(req) {
  return `release=${encodeURIComponent(req.releaseId)}` +
    `&measure=${encodeURIComponent(req.measureId)}` +
    `&level=${req.level}${areaParamFor(req)}`;
}

function selectionQuery() { return selectionQueryFor(currentRequest()); }

/* ------------------------------------------------------------------ boot */

async function boot() {
  state.status = await api('/api/status');
  if (!state.status.releases.length) {
    $('startup').innerHTML = '<p class="blocked"><strong>No dataset is built yet.</strong>' +
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

  const rel = encodeURIComponent(state.releaseId);
  [state.dataset, state.catalog] = await Promise.all([
    api(`/api/dataset?release=${rel}`),
    api(`/api/catalog?release=${rel}`),
  ]);

  $('release-value').textContent =
    `${state.catalog.period_label} · ${state.catalog.product_label}`;
  if (!state.catalog.levels[state.level]) {
    state.level = state.catalog.level_order[0];
  }
  state.measureId = measures()[0]?.measure_id || null;

  if (STATIC) {
    const shared = decodeSharedView(location.hash, shareContext());
    if (shared) {
      state.level = shared.level; state.measureId = shared.measure;
      state.areas = [...shared.areas]; state.benchmarkId = shared.benchmark;
      state.pick = shared.pick; state.compare = [...shared.compare];
    }
    applyStaticMode();
  }
  wireControls();
  renderLevelSwitch();
  renderSidebar();
  updateActions();
  $('startup').hidden = true;
  $('shell').hidden = false;
  await loadBenchmarks();
  await refresh();
  renderProjects();
  renderFooter();
}

/**
 * Turn off what a published copy cannot do, and say so on the page.
 *
 * The explore journey — map, search, inspector, comparison inside one
 * release, margins of error, denominators, coverage and source details — is
 * the whole published dataset and behaves exactly as it does locally. The
 * export bundle and saved views still require the service.
 */
function applyStaticMode() {
  const reasons = STATIC.unsupported || {};
  $('btn-save').hidden = true;
  $('save-panel').hidden = true;
  $('btn-share').hidden = false;
  $('btn-share').addEventListener('click', shareView);
  $('share-close').addEventListener('click', () => {
    $('share-result').hidden = true; $('btn-share').setAttribute('aria-expanded', 'false');
    $('btn-share').focus();
  });
  $('share-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('share-url').value); toast('Link copied.'); }
    catch (_) { $('share-url').focus(); $('share-url').select(); toast('Select and copy the link from the text field.'); }
  });
  window.addEventListener('hashchange', () => { if (location.hash.startsWith('#view=')) location.reload(); });
  $('btn-export').textContent = 'Download CSV';
  $('btn-export').title = reasons.export || '';

  const note = $('static-note');
  note.hidden = false;
  note.innerHTML =
    '<strong>Published copy.</strong> The map, the measures, the place search, ' +
    'the inspector and same-period comparisons all run on the full verified ' +
    'dataset. Share a view or open a printable brief here. Two features need the local app. ' +
    '<details><summary>Which, and why</summary><ul>' +
    `<li><strong>Export bundle</strong> — ${esc(reasons.export || '')}</li>` +
    `<li><strong>Saved views</strong> — ${esc(reasons.projects || '')}</li>` +
    '</ul></details>';
}

function wireControls() {
  document.querySelectorAll('.level-switch button').forEach((b) => {
    b.addEventListener('click', () => setLevel(b.dataset.level));
  });
  $('measure-search').addEventListener('input', (e) => {
    state.measureFilter = e.target.value.trim().toLowerCase();
    renderSidebar();
  });
  document.querySelectorAll('.chip').forEach((c) => {
    c.addEventListener('click', () => {
      state.unitFilter = c.dataset.unit;
      document.querySelectorAll('.chip').forEach((x) => {
        x.setAttribute('aria-pressed', String(x.dataset.unit === state.unitFilter));
      });
      renderSidebar();
    });
  });
  $('benchmark-select').addEventListener('change', async (e) => {
    state.benchmarkId = e.target.value;
    await refresh();
  });
  $('btn-method').addEventListener('click', () => toggleDrawer());
  if (STATIC) $('btn-save').replaceWith($('btn-save').cloneNode(true));
  $('drawer-close').addEventListener('click', () => toggleDrawer(false));
  $('btn-export').addEventListener('click', doExport);
  $('btn-brief').addEventListener('click', openBrief);
  $('export-dismiss').addEventListener('click', () => {
    $('export-result').hidden = true;
  });
  // Wrapped: passing the click event straight through made its MouseEvent the
  // `force` argument, which is always truthy, so the button only ever opened
  // the panel and never closed it.
  $('btn-save').addEventListener('click', () => toggleSavePanel());
  $('save-form').addEventListener('submit', saveProject);
  $('zoom-in').addEventListener('click', () => zoomBy(1.4));
  $('zoom-out').addEventListener('click', () => zoomBy(1 / 1.4));
  $('zoom-fit').addEventListener('click', fitMap);
  wirePlaceSearch();
  wireMapGestures();
  wireMeasureListKeys();
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!$('drawer').hidden) { toggleDrawer(false); return; }
    if (!$('save-panel').hidden) { toggleSavePanel(false); return; }
    if (!$('place-results').hidden) { closePlaceResults(); }
  });
}

/* ------------------------------------------------------------ level + sidebar */

function renderLevelSwitch() {
  document.querySelectorAll('.level-switch button').forEach((b) => {
    const on = b.dataset.level === state.level;
    b.setAttribute('aria-checked', String(on));
    const info = state.catalog.levels[b.dataset.level];
    b.disabled = !info;
  });
  const info = levelInfo();
  $('level-note').textContent =
    `${info.area_count.toLocaleString('en-US')} ${info.label.toLowerCase()} — ${info.note}.`;
}

async function setLevel(level) {
  if (level === state.level || !state.catalog.levels[level]) return;
  state.level = level;
  // A place chosen at one level does not exist at another, and neither does a
  // comparison built from it.
  state.areas = [];
  state.pick = null;
  state.compare = [];
  state.placeQuery = '';
  $('place-search').value = '';
  closePlaceResults();
  if (!measures().some((m) => m.measure_id === state.measureId)) {
    state.measureId = measures()[0]?.measure_id || null;
  }
  renderLevelSwitch();
  renderSidebar();
  await loadBenchmarks();
  await refresh();
}

function sidebarGroups() {
  const needle = state.measureFilter;
  const unitOk = (m) => state.unitFilter === 'all' || m.unit === state.unitFilter;
  const groups = levelInfo().groups.map((g) => ({
    heading: g.heading,
    all: g.measures.filter(unitOk),
  }));
  if (!needle) {
    return groups.map((g) => ({ heading: g.heading, measures: g.all }))
      .filter((g) => g.measures.length);
  }
  // A name match beats a description match. Typing "naturalis" should land on
  // the naturalised measure, not on every measure whose definition happens to
  // mention naturalisation; the wider match still runs when nothing is named.
  const byLabel = groups
    .map((g) => ({
      heading: g.heading,
      measures: g.all.filter((m) => m.label.toLowerCase().includes(needle)),
    }))
    .filter((g) => g.measures.length);
  if (byLabel.length) return byLabel;
  return groups
    .map((g) => ({
      heading: g.heading,
      measures: g.all.filter((m) =>
        `${m.counts_what} ${m.out_of} ${g.heading} ${m.tables.join(' ')}`
          .toLowerCase().includes(needle)),
    }))
    .filter((g) => g.measures.length);
}

function renderSidebar() {
  const host = $('topic-groups');
  host.textContent = '';
  const groups = sidebarGroups();
  const shown = groups.reduce((n, g) => n + g.measures.length, 0);
  $('measure-count').textContent = shown
    ? `${shown} of ${levelInfo().measure_count} measures`
    : '';

  if (!groups.length) {
    host.innerHTML = '<p class="empty-state">No measure matches that search. ' +
      'Clear the box or choose <em>All</em> to see every measure this build carries.</p>';
    return;
  }
  groups.forEach((g) => {
    const box = document.createElement('div');
    box.className = 'topic-group';
    const h = document.createElement('p');
    h.className = 'group-heading';
    h.textContent = g.heading;
    box.appendChild(h);
    g.measures.forEach((m) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'measure';
      b.setAttribute('role', 'option');
      b.setAttribute('aria-selected', String(m.measure_id === state.measureId));
      // One tab stop for the whole list. Forty-seven stops between the search
      // box and the map is not keyboard access, it is a keyboard obstacle.
      b.tabIndex = m.measure_id === state.measureId ? 0 : -1;
      // Labels wrap: a clipped measure name is exactly the thing a reader must
      // not have to guess at.
      b.innerHTML = `<span class="m-label">${esc(m.label)}</span>` +
        `<span class="m-sub">${esc(m.unit === 'percent'
          ? `share, out of ${m.out_of}` : 'number of people')}</span>`;
      b.addEventListener('click', () => chooseMeasure(m.measure_id));
      box.appendChild(b);
    });
    host.appendChild(box);
  });
  if (!host.querySelector('[tabindex="0"]')) {
    const first = host.querySelector('.measure');
    if (first) first.tabIndex = 0;
  }
}

function wireMeasureListKeys() {
  const host = $('topic-groups');
  host.addEventListener('keydown', (e) => {
    if (!e.target.classList.contains('measure')) return;
    const items = [...host.querySelectorAll('.measure')];
    const i = items.indexOf(e.target);
    const go = (j) => {
      const next = items[Math.max(0, Math.min(items.length - 1, j))];
      if (!next) return;
      items.forEach((x) => { x.tabIndex = -1; });
      next.tabIndex = 0;
      next.focus();
    };
    if (e.key === 'ArrowDown') { e.preventDefault(); go(i + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); go(i - 1); }
    else if (e.key === 'Home') { e.preventDefault(); go(0); }
    else if (e.key === 'End') { e.preventDefault(); go(items.length - 1); }
  });
}

async function chooseMeasure(measureId) {
  if (measureId === state.measureId) return;
  state.measureId = measureId;
  renderSidebar();
  await refresh();
}

/* ----------------------------------------------------------- place search */

function placeMatches(query) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return state.dataset.areas
    .filter((a) => a.level === state.level)
    .filter((a) => a.name.toLowerCase().includes(q) || a.geoid.includes(q))
    .slice(0, 12);
}

function wirePlaceSearch() {
  const input = $('place-search');
  input.addEventListener('input', () => {
    state.placeQuery = input.value;
    renderPlaceResults();
    renderTable();
  });
  input.addEventListener('focus', renderPlaceResults);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.place-find')) closePlaceResults();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') {
      const first = $('place-results').querySelector('button');
      if (first) { e.preventDefault(); first.focus(); }
    }
  });
  // An open list sits over the map, so it must also close when focus leaves
  // it — otherwise it keeps swallowing clicks meant for what is underneath.
  $('place-search').closest('.place-find').addEventListener('focusout', (e) => {
    if (!e.relatedTarget || !e.relatedTarget.closest('.place-find')) {
      setTimeout(closePlaceResults, 120);
    }
  });
}

function closePlaceResults() {
  $('place-results').hidden = true;
  $('place-search').setAttribute('aria-expanded', 'false');
}

function renderPlaceResults() {
  const host = $('place-results');
  const input = $('place-search');
  const q = state.placeQuery.trim();
  host.textContent = '';
  if (!q) { closePlaceResults(); return; }

  const hits = placeMatches(q);
  if (!hits.length) {
    // A search that finds nothing must say what this build can and cannot
    // find, rather than leaving a blank box.
    const li = document.createElement('li');
    li.className = 'no-match';
    li.innerHTML = `No ${levelNoun(2)} match “${esc(q)}”. This build carries the ` +
      'five New York City boroughs and their census tracts by published name ' +
      'and GEOID. It has no address search and no neighbourhood boundaries.';
    host.appendChild(li);
  } else {
    hits.forEach((a) => {
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'option');
      b.innerHTML = `${esc(a.name)} <span class="r-id">${esc(a.geoid)}</span>`;
      b.addEventListener('click', () => {
        selectArea(a.geoid, { focus: true });
        input.value = '';
        state.placeQuery = '';
        closePlaceResults();
        renderTable();
      });
      li.appendChild(b);
      host.appendChild(li);
    });
  }
  host.hidden = false;
  input.setAttribute('aria-expanded', 'true');
}

/* ---------------------------------------------------------- data + render */

async function loadBenchmarks() {
  const req = currentRequest();
  await benchGate.run(
    () => api(`/api/benchmarks?release=${encodeURIComponent(req.releaseId)}` +
      `&level=${req.level}${areaParamFor(req)}`),
    {
      commit: (res) => {
        state.benchmarks = res.benchmarks;
        const sel = $('benchmark-select');
        sel.textContent = '';
        res.benchmarks.forEach((b) => {
          const option = new Option(b.label, b.benchmark_id);
          // A reference this build cannot compute stays in the list, visibly
          // unavailable: an option that silently vanishes reads as one that
          // never existed.
          if (b.available_in_static_build === false) {
            option.disabled = true;
            option.text = `${b.label} — needs the local app`;
          }
          sel.add(option);
        });
        const chosen = res.benchmarks.find((b) => b.benchmark_id === state.benchmarkId);
        if (!chosen || chosen.available_in_static_build === false) {
          state.benchmarkId = 'none';
        }
        sel.value = state.benchmarkId;
      },
      fail: (e) => {
        // A reference list that could not be loaded must not leave the last
        // selection's references on screen as if they still applied.
        state.benchmarks = [];
        state.benchmarkId = 'none';
        $('benchmark-select').textContent = '';
        toast(`The reference list could not be loaded: ${e.message}`, 8000);
      },
    });
}

/** Saving and exporting write the shown selection to disk; both wait for it. */
function updateActions() {
  const usable = state.ready && !state.loading;
  $('btn-export').disabled = !usable;
  // Exports and briefs require a single completed selection.
  $('btn-brief').disabled = !usable;
  $('btn-share').disabled = !usable;
  const submit = $('save-submit');
  if (submit) submit.disabled = !usable;
  const note = $('save-blocked');
  if (note) {
    note.hidden = usable;
    note.textContent = usable ? '' :
      'Saving waits for a complete view. Reopening a saved view still works.';
  }
}

function showBusy() {
  state.loading = true;
  $('map-empty').hidden = false;
  $('map-empty').textContent = 'Loading…';
}

/**
 * Take down everything that described the previous selection.
 *
 * A load that failed leaves nothing trustworthy behind: the table, the
 * inspector, the comparison and the source panel all described a selection
 * this one was meant to replace, and leaving them up would present them as
 * current.
 */
function clearCurrentView() {
  state.ready = false;
  state.values = null;
  state.geo = null;
  state.quality = null;
  state.selectionJson = null;
  state.questionId = null;
  state.benchmark = null;
  state.pick = null;
  state.compare = [];
  state.hover = null;
  state.breaks = { cuts: [], min: null, max: null, classes: 0 };
  map._svg = null; map._layer = null; map._bboxes = null; map._drawn = null;
  $('map').textContent = '';
  $('legend').textContent = '';
  $('map-caption').textContent = '';
  $('readout').textContent = '';
  $('table-head').textContent = '';
  $('table-body').textContent = '';
  $('table-title').textContent = '—';
  $('table-note').textContent = '';
  $('table-empty').hidden = true;
  $('place-card').textContent = '';
  $('compare-panel').textContent = '';
  $('benchmark-card').textContent = '';
  $('source-details').textContent = '';
  $('drawer-body').textContent = '';
  $('scope-bar').textContent = '';
  updateActions();
}

async function refresh() {
  if (!state.measureId) return;
  $('share-result').hidden = true; $('btn-share').setAttribute('aria-expanded', 'false');
  const req = currentRequest();
  await dataGate.run(async () => {
    const rel = encodeURIComponent(req.releaseId);
    const [values, geo, quality] = await Promise.all([
      api(`/api/values?release=${rel}&measure=${encodeURIComponent(req.measureId)}`),
      api(`/api/geography?release=${rel}&level=${req.level}`),
      api(`/api/quality?${selectionQueryFor(req)}&benchmark=${encodeURIComponent(req.benchmarkId)}`),
    ]);
    // Built from the snapshot, not from the live state: by now the reference
    // control may already hold a different value belonging to a newer load.
    let benchmark = null;
    if (req.benchmarkId && req.benchmarkId !== 'none') {
      benchmark = await api(`/api/benchmark?${selectionQueryFor(req)}` +
        `&benchmark=${encodeURIComponent(req.benchmarkId)}`);
    }
    return {
      req, values: values.values, geo, quality: quality.quality,
      selectionJson: quality.selection, questionId: quality.question_id, benchmark,
    };
  }, {
    start: () => { state.ready = false; updateActions(); showBusy(); },
    commit: (r) => {
      // One assignment block: nothing on screen is ever built from half of
      // one load and half of another.
      state.values = r.values;
      state.geo = r.geo;
      state.quality = r.quality;
      state.selectionJson = r.selectionJson;
      state.questionId = r.questionId;
      state.benchmark = r.benchmark;
      state.ready = true;
      $('blocked').hidden = true;
      document.body.classList.remove('stale');
      computeBreaks();
      render();
    },
    fail: (e) => {
      clearCurrentView();
      // The heading must name the measure the user asked for, not the last
      // one that happened to load: the sidebar already shows the new one
      // selected, and disagreeing with it reads as data that is still there.
      renderHeading(currentMeasure());
      document.body.classList.add('stale');
      showBlocked('This selection could not be shown', [
        e.message,
        'Nothing from the previous selection is still on screen, and saving ' +
        'and exporting stay unavailable until a selection loads.',
      ], { retry: true });
      $('map-empty').hidden = false;
      $('map-empty').textContent = 'Nothing to draw for this selection.';
    },
    settle: () => { state.loading = false; updateActions(); },
  });
}

function showBlocked(title, lines, opts = {}) {
  const el = $('blocked');
  el.hidden = false;
  el.innerHTML = `<strong>${esc(title)}</strong><ul>` +
    lines.map((l) => `<li>${esc(l)}</li>`).join('') + '</ul>';
  // Without this, a selection that failed can only be recovered by choosing a
  // different one and coming back: the controls all short-circuit when the
  // value they would set is already the value held.
  if (opts.retry) {
    const again = document.createElement('button');
    again.type = 'button';
    again.className = 'btn';
    again.textContent = 'Try again';
    again.addEventListener('click', () => refresh());
    el.appendChild(again);
  }
}

function usableValues() {
  return scopedGeoids()
    .map((g) => state.values?.[g])
    .filter((v) => v && v.es === 'ok')
    .map((v) => v.e);
}

function computeBreaks() {
  state.breaks = classBreaks(usableValues(), 5);
  state.cuts = state.breaks.cuts;
}

function classOf(value) { return classIndex(value, state.cuts); }

function render() {
  const m = currentMeasure();
  if (!m) return;
  renderHeading(m);
  renderScopeBar();
  drawMap();
  // After drawMap: the caption reports what this map actually drew.
  $('map-caption').textContent = [
    `${m.label}, ${levelNoun(2)}, ${state.catalog.period_label}.`,
    shadingSentence(m),
    'The table below lists the same values.',
    ...coverageNote(),
  ].filter(Boolean).join(' ');
  renderLegend(m);
  renderTable();
  renderPlaceCard();
  renderComparePanel();
  renderBenchmarkCard();
  renderSourceDetails();
  if (!$('drawer').hidden) renderDrawer();
}

/**
 * What the shading actually does, for this selection.
 *
 * The number of classes is a property of the values in view, not a constant.
 * One borough, or a set of areas that all share a value, has one class, and a
 * caption that promises five would describe a map nobody is looking at.
 */
function shadingSentence(m) {
  const { classes, min, max } = state.breaks;
  if (!classes) return 'No area in view has a usable estimate, so nothing is shaded.';
  if (classes === 1) {
    const only = fmt(min, m.unit);
    return `Every area in view holds the same value, ${only}, so the map shows ` +
      'one shade and no break points.';
  }
  return `Shading divides the areas in view into ${classes} classes by value, ` +
    `from ${fmt(min, m.unit)} to ${fmt(max, m.unit)}.`;
}

/**
 * Coverage, kept separate from the build-wide count.
 *
 * "Not drawn" is a fact about the areas in this view and this export. How many
 * areas the whole build cannot draw is a different fact, and attaching it to a
 * view that drew everything claims a shortfall this map does not have.
 */
function coverageNote() {
  const report = (state.dataset.join_reports || [])
    .find((r) => r.level === state.level) || {};
  const scoped = scopedGeoids();
  const drawn = map._drawn || new Set();
  const unmatchedSet = new Set(report.unmatched_observations || []);
  return coverageSentences({
    inViewTotal: scoped.length,
    inViewDrawn: scoped.filter((g) => drawn.has(g)).length,
    datasetUnmatched: report.unmatched_observation_count || 0,
    datasetTotal: report.observations_total || 0,
    unmatchedInView: scoped.filter((g) => unmatchedSet.has(g)).length,
    noun: state.level === 'county' ? 'borough' : 'census tract',
    nounPlural: state.level === 'county' ? 'boroughs' : 'census tracts',
    vintage: state.dataset.release.boundary_release,
  });
}

/** The heading names the measure that was asked for, loaded or not. */
function renderHeading(m) {
  $('measure-title').textContent = m ? m.label : '—';
  if (!m) { $('denominator-line').textContent = ''; return; }
  $('denominator-line').textContent = m.unit === 'percent'
    ? `Percent of ${m.out_of} · ${state.catalog.period_label}`
    : `Number of people · ${state.catalog.period_label}`;
}

function renderScopeBar() {
  const host = $('scope-bar');
  host.textContent = '';
  const n = scopedGeoids().length;
  const total = levelInfo().area_count;
  const text = document.createElement('span');
  text.className = 'scope-text';
  // The public brief explicitly caps its printed table at 25 rows.
  const covers = STATIC
    ? 'The CSV covers exactly these; the brief lists up to 25.'
    : 'Exports and the brief cover exactly these.';
  text.textContent = state.areas.length
    ? `Showing ${n} of ${total.toLocaleString('en-US')} ${levelNoun(total)}: ` +
      `${state.areas.map(areaName).join(', ')}. ${covers}`
    : `Showing all ${n.toLocaleString('en-US')} ${levelNoun(n)}. ${covers}`;
  host.appendChild(text);
  if (state.areas.length) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn ghost';
    b.textContent = `Show all ${levelNoun(2)}`;
    b.addEventListener('click', () => setAreas([]));
    host.appendChild(b);
  }
}

async function setAreas(areas) {
  state.areas = areas;
  await loadBenchmarks();
  await refresh();
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

const MAP_W = 900;
const MAP_H = 620;
const SVG_NS = 'http://www.w3.org/2000/svg';

function drawMap() {
  const host = $('map');
  host.textContent = '';
  const empty = $('map-empty');
  const visible = new Set(scopedGeoids());
  const features = (state.geo?.features || []).filter((f) => visible.has(f.properties.GEOID));
  if (!features.length) {
    empty.hidden = false;
    empty.textContent = 'No boundary layer is available for these areas at this ' +
      'geography vintage. The table below still carries every value.';
    map._bboxes = null;
    map._drawn = new Set();
    return;
  }
  empty.hidden = true;

  let minx = Infinity; let miny = Infinity; let maxx = -Infinity; let maxy = -Infinity;
  const walk = (n) => {
    if (typeof n[0] === 'number') {
      minx = Math.min(minx, n[0]); maxx = Math.max(maxx, n[0]);
      miny = Math.min(miny, n[1]); maxy = Math.max(maxy, n[1]);
    } else n.forEach(walk);
  };
  features.forEach((f) => f.geometry && walk(f.geometry.coordinates));

  const pad = 12;
  const sx = Math.cos(((miny + maxy) / 2) * Math.PI / 180);
  const scale = Math.min((MAP_W - 2 * pad) / ((maxx - minx) * sx),
                         (MAP_H - 2 * pad) / (maxy - miny));
  const ox = pad + ((MAP_W - 2 * pad) - (maxx - minx) * sx * scale) / 2;
  const oy = pad + ((MAP_H - 2 * pad) - (maxy - miny) * scale) / 2;
  const project = (x, y) => [ox + (x - minx) * sx * scale, oy + (maxy - y) * scale];

  const m = currentMeasure();
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${MAP_W} ${MAP_H}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label',
    `${m.label}, ${levelNoun(2)}. The table below lists the same values.`);

  const g = document.createElementNS(SVG_NS, 'g');
  g.id = 'map-layer';
  svg.appendChild(g);

  if (state.geo.metadata && state.geo.metadata.synthetic) {
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('x', '12'); t.setAttribute('y', '22');
    t.setAttribute('font-size', '14'); t.setAttribute('fill', '#8a4b12');
    t.setAttribute('font-weight', '700');
    t.textContent = 'SYNTHETIC SHAPES — not boundaries';
    svg.appendChild(t);
  }

  // Per-shape keyboard stops are useful for five boroughs and hostile for two
  // thousand tracts; at that size the table and the place search are the
  // keyboard route, and the caption says so.
  const perShapeFocus = features.length <= 60;
  const bboxes = {};

  features.forEach((f) => {
    if (!f.geometry) return;
    const geoid = f.properties.GEOID;
    const v = state.values?.[geoid];
    const est = v && v.es === 'ok' ? v.e : null;
    const cls = classOf(est);
    const p = document.createElementNS(SVG_NS, 'path');
    p.setAttribute('d', projectPath(f.geometry, project));
    if (cls === null) {
      p.setAttribute('fill', 'var(--c-none)');
      p.setAttribute('stroke', 'var(--c-none-line)');
      p.setAttribute('stroke-dasharray', '2 2');
    } else {
      p.setAttribute('fill', rampColour(cls));
      p.setAttribute('stroke', '#ffffff');
    }
    p.setAttribute('stroke-width', '0.6');
    p.setAttribute('vector-effect', 'non-scaling-stroke');
    p.dataset.geoid = geoid;
    const label = `${f.properties.name}: ` +
      (est === null ? (v ? plainReason(v, 'e') : 'no usable estimate') : fmt(est, m.unit));
    if (perShapeFocus) {
      p.setAttribute('tabindex', '0');
      p.setAttribute('role', 'button');
      p.setAttribute('aria-label', label);
    } else {
      const title = document.createElementNS(SVG_NS, 'title');
      title.textContent = label;
      p.appendChild(title);
    }
    g.appendChild(p);
    const bb = [Infinity, Infinity, -Infinity, -Infinity];
    const walk2 = (n) => {
      if (typeof n[0] === 'number') {
        const [px, py] = project(n[0], n[1]);
        bb[0] = Math.min(bb[0], px); bb[1] = Math.min(bb[1], py);
        bb[2] = Math.max(bb[2], px); bb[3] = Math.max(bb[3], py);
      } else n.forEach(walk2);
    };
    walk2(f.geometry.coordinates);
    bboxes[geoid] = bb;
  });

  host.appendChild(svg);
  map._svg = svg;
  map._layer = g;
  map._bboxes = bboxes;
  //: Exactly the areas this map drew, so the caption can say what it did and
  //: did not cover without guessing from a build-wide join report.
  map._drawn = new Set(Object.keys(bboxes));
  applyView();
  markMapSelection();
}

const map = { _svg: null, _layer: null, _bboxes: null, _drawn: null };

function applyView() {
  if (!map._layer) return;
  const { k, x, y } = state.view;
  map._layer.setAttribute('transform', `translate(${x} ${y}) scale(${k})`);
}

function clampView() {
  const v = state.view;
  v.k = Math.min(24, Math.max(1, v.k));
  // Keep at least a quarter of the drawing on screen in each direction.
  const maxPan = { x: MAP_W * v.k - MAP_W * 0.25, y: MAP_H * v.k - MAP_H * 0.25 };
  v.x = Math.min(MAP_W * 0.25, Math.max(-maxPan.x, v.x));
  v.y = Math.min(MAP_H * 0.25, Math.max(-maxPan.y, v.y));
}

function zoomAt(factor, cx, cy) {
  const v = state.view;
  const k0 = v.k;
  v.k = Math.min(24, Math.max(1, k0 * factor));
  const ratio = v.k / k0;
  v.x = cx - (cx - v.x) * ratio;
  v.y = cy - (cy - v.y) * ratio;
  clampView();
  applyView();
}

function zoomBy(factor) { zoomAt(factor, MAP_W / 2, MAP_H / 2); }

function fitMap() {
  state.view = { k: 1, x: 0, y: 0 };
  applyView();
}

function focusOnArea(geoid) {
  const bb = map._bboxes && map._bboxes[geoid];
  if (!bb) return;
  const w = Math.max(bb[2] - bb[0], 1);
  const h = Math.max(bb[3] - bb[1], 1);
  const k = Math.min(12, Math.max(1, Math.min(MAP_W / (w * 2.4), MAP_H / (h * 2.4))));
  const cx = (bb[0] + bb[2]) / 2;
  const cy = (bb[1] + bb[3]) / 2;
  state.view = { k, x: MAP_W / 2 - cx * k, y: MAP_H / 2 - cy * k };
  clampView();
  applyView();
}

function svgPoint(evt) {
  const svg = map._svg;
  if (!svg) return { x: MAP_W / 2, y: MAP_H / 2 };
  const r = svg.getBoundingClientRect();
  // preserveAspectRatio="xMidYMid meet": one shared scale, centred.
  const s = Math.min(r.width / MAP_W, r.height / MAP_H);
  return {
    x: (evt.clientX - r.left - (r.width - MAP_W * s) / 2) / s,
    y: (evt.clientY - r.top - (r.height - MAP_H * s) / 2) / s,
  };
}

function wireMapGestures() {
  const host = $('map');
  let drag = null;

  host.addEventListener('wheel', (e) => {
    if (!map._svg) return;
    e.preventDefault();
    const p = svgPoint(e);
    zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, p.x, p.y);
  }, { passive: false });

  host.addEventListener('pointerdown', (e) => {
    if (!map._svg || e.button !== 0) return;
    // The GEOID is read here, at the press, and not at the release: pointer
    // capture retargets every later event in the gesture to the map container,
    // so `pointerup` never carries the shape that was pressed. Reading it at
    // release left clicking the map dead while hover still worked.
    drag = {
      id: e.pointerId, x: e.clientX, y: e.clientY, moved: 0,
      geoid: (e.target.dataset && e.target.dataset.geoid) || null,
    };
    host.setPointerCapture(e.pointerId);
    host.classList.add('dragging');
  });
  host.addEventListener('pointermove', (e) => {
    if (drag && drag.id === e.pointerId) {
      const r = map._svg.getBoundingClientRect();
      const s = Math.min(r.width / MAP_W, r.height / MAP_H) || 1;
      const dx = (e.clientX - drag.x) / s;
      const dy = (e.clientY - drag.y) / s;
      drag.moved += Math.abs(dx) + Math.abs(dy);
      drag.x = e.clientX; drag.y = e.clientY;
      state.view.x += dx; state.view.y += dy;
      clampView();
      applyView();
      return;
    }
    const geoid = e.target.dataset && e.target.dataset.geoid;
    setHover(geoid || null);
  });
  const endDrag = (e) => {
    if (!drag || drag.id !== e.pointerId) return;
    const { moved, geoid } = drag;
    drag = null;
    host.classList.remove('dragging');
    // A drag is a pan, not a click. Anything past a few pixels of travel
    // selects nothing, so panning across the city never changes the selection.
    if (moved < DRAG_SLOP && geoid) selectArea(geoid);
  };
  host.addEventListener('pointerup', endDrag);
  // A cancelled gesture (the browser taking over, a touch turning into a
  // scroll) selects nothing: only a completed press-and-release does.
  host.addEventListener('pointercancel', () => {
    drag = null; host.classList.remove('dragging');
  });
  host.addEventListener('pointerleave', () => setHover(null));

  host.addEventListener('focusin', (e) => {
    const geoid = e.target.dataset && e.target.dataset.geoid;
    if (geoid) setHover(geoid);
  });
  host.addEventListener('focusout', (e) => {
    if (e.target.dataset && e.target.dataset.geoid) setHover(null);
  });

  host.addEventListener('keydown', (e) => {
    const geoid = e.target.dataset && e.target.dataset.geoid;
    if (geoid && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault(); selectArea(geoid); return;
    }
    const step = 60 / state.view.k;
    const moves = {
      ArrowLeft: [step, 0], ArrowRight: [-step, 0],
      ArrowUp: [0, step], ArrowDown: [0, -step],
    };
    if (moves[e.key]) {
      e.preventDefault();
      state.view.x += moves[e.key][0] * state.view.k;
      state.view.y += moves[e.key][1] * state.view.k;
      clampView(); applyView(); return;
    }
    if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomBy(1.4); }
    else if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomBy(1 / 1.4); }
    else if (e.key === '0') { e.preventDefault(); fitMap(); }
  });
}

function setHover(geoid) {
  if (state.hover === geoid) return;
  state.hover = geoid;
  if (map._layer) {
    map._layer.querySelectorAll('path[data-hover]').forEach((p) => {
      delete p.dataset.hover;
    });
    if (geoid) {
      const p = map._layer.querySelector(`path[data-geoid="${CSS.escape(geoid)}"]`);
      if (p) p.dataset.hover = 'true';
    }
  }
  renderReadout();
}

function renderReadout() {
  const host = $('readout');
  const geoid = state.hover || state.pick;
  const m = currentMeasure();
  if (!geoid || !m) { host.textContent = ''; return; }
  const v = state.values?.[geoid];
  const value = v && v.es === 'ok' ? fmt(v.e, m.unit) : 'no data';
  const moe = v && v.es === 'ok'
    ? (isControlled(v) ? ' (controlled total, no sampling error)'
      : (v.ms === 'ok' ? ` ${fmtMoe(v.m, m.unit)}` : ' (margin of error unavailable)'))
    : '';
  host.innerHTML = `<span class="r-name">${esc(areaName(geoid))}</span>` +
    `<span class="r-val">${esc(value)}${esc(moe)}</span>`;
}

function markMapSelection() {
  if (!map._layer) return;
  map._layer.querySelectorAll('path[data-pick], path[data-cmp]').forEach((p) => {
    delete p.dataset.pick; delete p.dataset.cmp;
  });
  state.compare.forEach((g, i) => {
    const p = map._layer.querySelector(`path[data-geoid="${CSS.escape(g)}"]`);
    if (p) p.dataset.cmp = i === 0 ? 'a' : 'b';
  });
  if (state.pick) {
    const p = map._layer.querySelector(`path[data-geoid="${CSS.escape(state.pick)}"]`);
    if (p) p.dataset.pick = 'true';
  }
}

function renderLegend(m) {
  const el = $('legend');
  const parts = [`<span class="lg-title">${m.unit === 'percent'
    ? `percent of ${esc(m.out_of)}` : 'people'}</span>`];
  // Each class is labelled by the range it covers, ends included. Showing only
  // the interior breaks leaves a reader guessing where the first and last
  // classes start and stop.
  legendRanges(state.breaks).forEach(({ from, to }, i) => {
    const label = from === to ? fmt(from, m.unit)
      : `${fmt(from, m.unit)}–${fmt(to, m.unit)}`;
    const fill = rampColour(i);
    parts.push('<span class="lg-class"><span class="swatch" ' +
      `style="background:${fill}"></span>` +
      `<span class="lg-num">${esc(label)}</span></span>`);
  });
  const missing = scopedGeoids().filter((g) => !(state.values?.[g]?.es === 'ok')).length;
  parts.push('<span class="lg-class"><span class="nodata"></span>' +
    `<span>no usable estimate (${missing})</span></span>`);
  el.innerHTML = parts.join(' ');
}

/* ----------------------------------------------------------------- table */

const COLUMNS = [
  { key: 'name', label: 'Area', num: false },
  { key: 'estimate', label: 'Estimate', num: true },
  { key: 'moe', label: 'Margin of error (90%)', num: true },
  { key: 'quality', label: 'Reliability', num: false },
];

function rowsForTable() {
  const m = currentMeasure();
  const q = state.placeQuery.trim().toLowerCase();
  let rows = scopedGeoids().map((geoid) => {
    const v = state.values?.[geoid] || {};
    return {
      geoid,
      name: areaName(geoid),
      estimate: v.es === 'ok' ? v.e : null,
      moe: v.ms === 'ok' ? v.m : null,
      controlled: isControlled(v),
      quality: reliabilityWords(v, m.unit),
    };
  });
  if (q) {
    rows = rows.filter((r) => r.name.toLowerCase().includes(q) || r.geoid.includes(q));
  }
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

function renderTable() {
  const m = currentMeasure();
  if (!m || !state.values) return;
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
      state.sort = {
        key: c.key,
        dir: state.sort.key === c.key && state.sort.dir === 'desc' ? 'asc' : 'desc',
      };
      renderTable();
    });
    th.appendChild(b);
    tr.appendChild(th);
  });
  head.appendChild(tr);

  const body = $('table-body');
  body.textContent = '';
  if (state.benchmark) body.appendChild(referenceRow(m));

  const rows = rowsForTable();
  const shown = rows.slice(0, MAX_TABLE_ROWS);
  shown.forEach((row) => {
    const tr2 = document.createElement('tr');
    tr2.tabIndex = 0;
    tr2.dataset.geoid = row.geoid;
    if (state.pick === row.geoid) tr2.dataset.pick = 'true';
    tr2.addEventListener('click', () => selectArea(row.geoid));
    tr2.addEventListener('focus', () => setHover(row.geoid));
    tr2.addEventListener('blur', () => setHover(null));
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
        td.textContent = c.key === 'moe' ? fmtMoe(row[c.key], m.unit) : fmt(row[c.key], m.unit);
      }
      tr2.appendChild(td);
    });
    body.appendChild(tr2);
  });

  const filtering = state.placeQuery.trim().length > 0;
  const total = scopedGeoids().length;
  $('table-title').textContent = filtering
    ? `${rows.length.toLocaleString('en-US')} of ${total.toLocaleString('en-US')} ${levelNoun(total)}`
    : `${total.toLocaleString('en-US')} ${levelNoun(total)}`;
  const missing = rows.filter((r) => r.estimate === null).length;
  $('table-note').textContent =
    `Values are ${m.unit === 'percent' ? `percentages of ${m.out_of}` : 'counts of people'}. ` +
    `${missing} area${missing === 1 ? '' : 's'} here have no usable estimate and read ` +
    '“no data”, never zero; sorting keeps them at the end.' +
    (rows.length > shown.length
      ? ` Listing the first ${shown.length}; search by name or GEOID to reach the rest, and the export contains all.`
      : '');
  const emptyEl = $('table-empty');
  if (!rows.length) {
    emptyEl.hidden = false;
    emptyEl.textContent = filtering
      ? `No ${levelNoun(2)} in view match “${state.placeQuery.trim()}”. Clear the search to see all ${total.toLocaleString('en-US')} ${levelNoun(total)} in view.`
      : 'No areas are in view.';
  } else {
    emptyEl.hidden = true;
  }
}

function referenceRow(m) {
  const b = state.benchmark;
  const tr = document.createElement('tr');
  tr.className = 'reference-row';
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
    moeText = fmtMoe(b.moe, m.unit);
    note = 'reference value';
  }
  [b.label, b.estimate === null ? 'no data' : fmt(b.estimate, m.unit), moeText, note]
    .forEach((text, i) => {
      const td = document.createElement('td');
      if (i === 1 || i === 2) td.className = 'num';
      td.textContent = text;
      tr.appendChild(td);
    });
  return tr;
}

/* --------------------------------------------------------- inspect panel */

function selectArea(geoid, opts = {}) {
  dismissShare();
  state.pick = geoid;
  if (opts.focus) focusOnArea(geoid);
  markMapSelection();
  renderReadout();
  renderPlaceCard();
  renderTable();
  if (!$('drawer').hidden) renderDrawer();
}

function clearPick() {
  dismissShare();
  state.pick = null;
  markMapSelection();
  renderReadout();
  renderPlaceCard();
  renderTable();
}

function valueRows(geoid, m) {
  const v = state.values?.[geoid] || {};
  const out = [];
  if (v.n !== undefined && v.n !== null) {
    out.push(['Counted', `${fmt(v.n, 'persons')} people`]);
    out.push([`Out of (${m.out_of || 'the universe'})`, `${fmt(v.d, 'persons')} people`]);
  }
  out.push(['Reliability', reliabilityWords(v, m.unit)]);
  out.push(['Area code (GEOID)', geoid]);
  return out;
}

function renderPlaceCard() {
  const host = $('place-card');
  const m = currentMeasure();
  if (!m) { host.textContent = ''; return; }
  if (!state.pick) {
    host.innerHTML = '<p class="empty-state">Choose an area on the map, in the ' +
      'table, or with the place search to see its estimate, its margin of error ' +
      'and the denominator behind it.</p>';
    return;
  }
  const geoid = state.pick;
  const v = state.values?.[geoid] || {};
  const parts = [];
  parts.push(`<p class="pc-name">${esc(areaName(geoid))}</p>`);
  if (v.es === 'ok') {
    parts.push(`<p class="pc-value">${esc(fmt(v.e, m.unit))}</p>`);
    if (isControlled(v)) {
      parts.push('<p class="pc-moe">no sampling error — controlled to an ' +
        'independent population estimate</p>');
    } else if (v.ms === 'ok') {
      parts.push(`<p class="pc-moe">${esc(fmtMoe(v.m, m.unit))} at 90% confidence</p>`);
    } else {
      parts.push('<p class="pc-moe pc-caveat">margin of error unavailable — ' +
        `${esc(plainReason(v, 'm'))}. Missing uncertainty is unavailable, not zero.</p>`);
    }
  } else {
    parts.push('<p class="pc-value">no data</p>');
    parts.push(`<p class="pc-moe pc-caveat">${esc(plainReason(v, 'e'))}</p>`);
  }
  parts.push(`<p class="pc-unit">${esc(m.unit === 'percent'
    ? `Percent of ${m.out_of}` : 'Number of people, not a share')} · ` +
    `${esc(state.catalog.period_label)}</p>`);
  parts.push('<dl class="pc-rows">' + valueRows(geoid, m)
    .map(([k, val]) => `<div><dt>${esc(k)}</dt><dd>${esc(val)}</dd></div>`).join('') + '</dl>');
  host.innerHTML = parts.join('');

  const actions = document.createElement('div');
  actions.className = 'pc-actions';
  const inCompare = state.compare.includes(geoid);
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'btn';
  add.textContent = inCompare ? 'Remove from comparison' : 'Add to comparison';
  add.addEventListener('click', () => (inCompare ? removeFromCompare(geoid) : addToCompare(geoid)));
  actions.appendChild(add);

  const onlyThis = state.areas.length === 1 && state.areas[0] === geoid;
  const limit = document.createElement('button');
  limit.type = 'button';
  limit.className = 'btn';
  limit.textContent = onlyThis ? `Show all ${levelNoun(2)}` : 'Show only this place';
  limit.addEventListener('click', () => setAreas(onlyThis ? [] : [geoid]));
  actions.appendChild(limit);

  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'btn ghost';
  clear.textContent = 'Clear selection';
  clear.addEventListener('click', clearPick);
  actions.appendChild(clear);
  host.appendChild(actions);
}

function addToCompare(geoid) {
  dismissShare();
  if (state.compare.includes(geoid)) return;
  if (state.compare.length >= 2) {
    toast('Two places at a time. Remove one first — a comparison a reader can ' +
      'check is a comparison they can hold in their head.');
    return;
  }
  state.compare.push(geoid);
  markMapSelection();
  renderComparePanel();
  renderPlaceCard();
}

function removeFromCompare(geoid) {
  dismissShare();
  state.compare = state.compare.filter((g) => g !== geoid);
  markMapSelection();
  renderComparePanel();
  renderPlaceCard();
}

function renderComparePanel() {
  const host = $('compare-panel');
  const m = currentMeasure();
  host.textContent = '';
  if (!m) return;
  if (!state.compare.length) {
    host.innerHTML = '<p class="empty-state">Add up to two places to compare them ' +
      'side by side inside this one reference period.</p>';
    return;
  }
  state.compare.forEach((geoid, i) => {
    const v = state.values?.[geoid] || {};
    const slot = document.createElement('div');
    slot.className = 'slot';
    slot.dataset.slot = i === 0 ? 'a' : 'b';
    const body = document.createElement('div');
    body.className = 'slot-body';
    const value = v.es === 'ok' ? fmt(v.e, m.unit) : 'no data';
    const moe = v.es === 'ok'
      ? (isControlled(v) ? 'no sampling error (controlled total)'
        : (v.ms === 'ok' ? `${fmtMoe(v.m, m.unit)} at 90% confidence`
          : 'margin of error unavailable'))
      : plainReason(v, 'e');
    body.innerHTML = `<div class="slot-name">${esc(areaName(geoid))}</div>` +
      `<div class="slot-val">${esc(value)}</div>` +
      `<div class="muted tiny">${esc(moe)}</div>`;
    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'btn ghost';
    rm.textContent = 'Remove';
    rm.setAttribute('aria-label', `Remove ${areaName(geoid)} from the comparison`);
    rm.addEventListener('click', () => removeFromCompare(geoid));
    slot.append(body, rm);
    host.appendChild(slot);
  });

  if (state.compare.length === 2) {
    const [a, b] = state.compare.map((g) => state.values?.[g] || {});
    const line = document.createElement('div');
    line.className = 'diff-line';
    if (a.es === 'ok' && b.es === 'ok') {
      const diff = a.e - b.e;
      const unitWord = m.unit === 'percent' ? 'percentage points' : 'people';
      const size = m.unit === 'percent'
        ? Math.abs(diff).toFixed(1) : Math.round(Math.abs(diff)).toLocaleString('en-US');
      line.innerHTML =
        `<span class="diff-value">${esc(size)} ${esc(unitWord)}</span> ` +
        `${esc(diff >= 0 ? 'higher in' : 'lower in')} ${esc(areaName(state.compare[0]))} ` +
        `than ${esc(areaName(state.compare[1]))}.` +
        '<span class="diff-note">This is the difference between two published ' +
        'estimates in the same reference period. This build does not test whether ' +
        'that difference is statistically significant, so do not describe it as ' +
        'one. Read both margins of error above.</span>';
    } else {
      line.innerHTML = '<span class="diff-value">No difference is shown.</span>' +
        '<span class="diff-note">At least one of these places has no published ' +
        'estimate for this measure, and a missing value is not a zero.</span>';
    }
    host.appendChild(line);

    const limit = document.createElement('div');
    limit.className = 'pc-actions';
    const both = document.createElement('button');
    both.type = 'button';
    both.className = 'btn';
    const isLimited = state.areas.length === 2 &&
      state.compare.every((g) => state.areas.includes(g));
    both.textContent = isLimited ? `Show all ${levelNoun(2)}` : 'Show only these two';
    both.addEventListener('click', () => setAreas(isLimited ? [] : [...state.compare]));
    const clearBoth = document.createElement('button');
    clearBoth.type = 'button';
    clearBoth.className = 'btn ghost';
    clearBoth.textContent = 'Clear comparison';
    clearBoth.addEventListener('click', () => {
      dismissShare();
      state.compare = [];
      markMapSelection();
      renderComparePanel();
      renderPlaceCard();
    });
    limit.append(both, clearBoth);
    host.appendChild(limit);
  }
}

function renderBenchmarkCard() {
  const host = $('benchmark-card');
  const m = currentMeasure();
  const chosen = state.benchmarks.find((b) => b.benchmark_id === state.benchmarkId);
  if (!state.benchmark) {
    host.innerHTML = `<p class="muted tiny">${esc(chosen ? chosen.description : '')}</p>`;
    return;
  }
  const b = state.benchmark;
  if (!b.available) {
    host.innerHTML = `<p class="pc-caveat">${esc(b.label)} is not available here.</p>` +
      `<p class="muted tiny">${esc(b.unavailable_reason || 'no reason recorded')}</p>`;
    return;
  }
  const moe = b.controlled ? 'no sampling error (controlled total)'
    : (b.moe_status === 'ok' ? `${fmtMoe(b.moe, m.unit)} at 90% confidence`
      : 'margin of error unavailable');
  host.innerHTML = `<p class="pc-name">${esc(b.label)}</p>` +
    `<p class="pc-value">${esc(b.estimate === null ? 'no data' : fmt(b.estimate, m.unit))}</p>` +
    `<p class="pc-moe">${esc(moe)}</p>` +
    `<p class="muted tiny">${esc(b.basis)}</p>`;
}

function renderSourceDetails() {
  const m = currentMeasure();
  const full = state.dataset.measures.find((x) => x.measure_id === state.measureId);
  if (!m || !full) return;
  const cells = (full.cells || []).map((c) =>
    `<li>${esc(c.cell)} — ${esc(c.label)}<br><span class="muted">estimate ` +
    `${esc(c.estimate_var)}, margin of error ${esc(c.moe_var || 'none published')}` +
    `</span></li>`).join('');
  $('source-details').innerHTML =
    `<dl><dt>Published universe</dt><dd>${esc(full.universe_published?.[0] || full.universe_note)}</dd>` +
    `<dt>Measure denominator</dt><dd>${esc(m.unit === 'percent' ? m.out_of : 'not applicable — this is a count')}</dd>` +
    `<dt>Definition</dt><dd>${esc(full.definition_note)}</dd>` +
    `<dt>Tables</dt><dd>${esc((full.tables || []).join(', '))}</dd>` +
    `<dt>Numerator cells</dt><dd class="codes">${esc((full.numerator_cells || []).join(' + '))}</dd>` +
    `<dt>Denominator cells</dt><dd class="codes">${esc((full.denominator_cells || []).join(' + ') || 'not applicable')}</dd>` +
    `<dt>Source</dt><dd>${esc(state.dataset.release.citation)}</dd></dl>` +
    `<ul class="codes">${cells}</ul>`;
}

/* ---------------------------------------------------------------- drawer */

function toggleDrawer(force) {
  const d = $('drawer');
  const open = force === undefined ? d.hidden : force;
  d.hidden = !open;
  $('btn-method').setAttribute('aria-expanded', String(open));
  if (open) { renderDrawer(); $('drawer-close').focus(); }
  else $('btn-method').focus();
}

function toggleSavePanel(force) {
  const p = $('save-panel');
  const open = force === undefined ? p.hidden : force;
  p.hidden = !open;
  $('btn-save').setAttribute('aria-expanded', String(open));
  if (open) { renderProjects(); $('project-id').focus(); }
}

function renderDrawer() {
  const m = currentMeasure();
  const q = state.quality;
  if (!m || !q) return;
  const full = state.dataset.measures.find((x) => x.measure_id === state.measureId);
  const parts = [];

  parts.push('<h3>What is being shown</h3>');
  parts.push(`<p><strong>${esc(m.label)}</strong></p>`);
  parts.push(`<p>We are counting ${esc(m.counts_what)}.</p>`);
  parts.push(`<p>${esc(m.unit === 'percent'
    ? `Shown as a percentage of ${m.out_of}.`
    : 'Shown as a number of people, not a share, so it reflects how large the place is as well as its composition.')}</p>`);
  parts.push(`<p>${esc(state.catalog.period_label)} — a five-year period estimate, ` +
    'not a single year.</p>');

  [['Uncertainty', q.uncertainty], ['Comparison', q.comparison], ['Period', q.freshness]]
    .forEach(([title, panel]) => {
      if (!panel) return;
      parts.push(`<h3>${esc(title)}</h3>`);
      parts.push(`<p class="state ${esc(panel.class)}">${esc(panel.state)}</p>`);
      panel.lines.forEach((line) => parts.push(`<p>${esc(line)}</p>`));
    });

  parts.push('<h3>What this does not say</h3>');
  if (state.level === 'tract') {
    parts.push('<p class="caveat">Census tracts are statistical areas, not ' +
      'neighbourhoods. This build has no documented neighbourhood boundaries.</p>');
    parts.push('<p class="caveat">Tract estimates carry large margins of error. ' +
      'Read the reliability column before quoting a single tract.</p>');
  }
  parts.push('<p class="caveat">A birthplace count is a stock: how many residents ' +
    'were born in that place. It is not a count of recent arrivals and says ' +
    'nothing about when or whether anyone moved here.</p>');
  parts.push('<p class="caveat">A difference between two places is not tested for ' +
    'statistical significance anywhere in this build.</p>');
  (full?.caveats || []).forEach((x) => parts.push(`<p class="caveat">${esc(x)}</p>`));

  parts.push('<h3>Source</h3>');
  parts.push(`<p>${esc(state.dataset.release.citation)}</p>`);
  parts.push(`<p>Boundaries: ${esc(state.dataset.release.geography_vintage)}. ` +
    'Generalized for display; not legal boundary descriptions.</p>');

  $('drawer-body').innerHTML = parts.join('');
}

/* --------------------------------------------------------- brief + export */

function dismissShare() {
  $('share-result').hidden = true; $('btn-share').setAttribute('aria-expanded', 'false');
}
function shareContext() {
  return {snapshot: window.CENSUS_EXPLORER_STATIC.snapshot, release: state.releaseId,
    catalog: state.catalog, areas: state.dataset.areas};
}
function shareView() {
  if (!state.ready || state.loading) return;
  try {
    const hash = encodeSharedView({v: 1, snapshot: shareContext().snapshot,
      release: state.releaseId, level: state.level, measure: state.measureId,
      areas: [...state.areas], benchmark: state.benchmarkId,
      pick: state.pick, compare: [...state.compare]}, shareContext());
    const url = new URL(location.href); url.hash = hash;
    $('share-url').value = url.href;
    $('share-result').hidden = false;
    $('btn-share').setAttribute('aria-expanded', 'true');
    $('share-url').focus(); $('share-url').select();
    toast('Link ready to copy. It restores this selection and checks the published snapshot.');
  } catch (e) { toast(e.message, 9000); }
}
function openPublishedBrief() {
  const full = state.dataset.measures.find(m => m.measure_id === state.measureId);
  const html = publishedBrief({dataset: state.dataset, measure: {...full, ...currentMeasure()},
    areas: scopedGeoids(), values: state.values, quality: state.quality,
    benchmark: state.benchmark, snapshot: shareContext().snapshot, compare: [...state.compare], coverage: coverageNote()});
  const url = URL.createObjectURL(new Blob([html], {type: 'text/html;charset=utf-8'}));
  window.open(url, '_blank', 'noopener');
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  toast('Brief opened in a new tab. Use your browser’s Print command to save it as PDF.', 8000);
}

/** Open the brief for exactly what is on screen, without writing anything. */
function openBrief() {
  if (!state.ready || state.loading) return;
  if (STATIC) return openPublishedBrief();
  const req = currentRequest();
  const url = `/api/brief?${selectionQueryFor(req)}` +
    `&question=${encodeURIComponent(state.questionId || '')}` +
    `&benchmark=${encodeURIComponent(req.benchmarkId || 'none')}`;
  window.open(url, '_blank', 'noopener');
  toast('The brief opened in a new tab. It is generated from what is on ' +
    'screen and is not saved; use Export to write it to disk, or your ' +
    'browser’s print dialog to save it as PDF.', 8000);
}

/**
 * The same rows the local service would export, written in the browser.
 *
 * Every number here was computed by the Python code during the build; this
 * only serialises them, in the exporter's own column order, and a test
 * compares the result against the exporter's own output.
 */
function downloadCsv() {
  const areas = scopedGeoids();
  const measure = state.dataset.measures.find((m) => m.measure_id === state.measureId);
  const names = {};
  const levels = {};
  state.dataset.areas.forEach((a) => { names[a.geoid] = a.name; levels[a.geoid] = a.level; });
  const text = buildCsv({
    release: state.dataset.release, measure, areas,
    areaNames: names, areaLevels: levels, values: state.values,
  });
  const name = `${state.measureId}-${state.level}-${state.releaseId}.csv`;
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);

  $('export-title').textContent = 'Data downloaded';
  $('export-what').textContent =
    `${measure.label} — ${areas.length.toLocaleString('en-US')} ` +
    `${levelNoun(areas.length)}, ${state.catalog.period_label}, saved as ${name}. ` +
    'One row per area, with the margin of error, the denominator and the ' +
    'published cell codes, exactly as the local app exports them.';
  $('export-files').textContent = '';
  $('export-where').textContent = (STATIC.unsupported || {}).export || '';
  $('export-result').hidden = false;
  $('export-result').scrollIntoView({ block: 'nearest' });
}

async function doExport() {
  if (!state.ready) return;
  if (STATIC) return downloadCsv();
  const areas = scopedGeoids();
  $('btn-export').disabled = true;
  try {
    const res = await post('/api/export', {
      release_id: state.releaseId,
      measure_id: state.measureId,
      level: state.level,
      areas,
      question_id: state.questionId,
      benchmark_id: state.benchmarkId,
      figure_kind: state.level === 'tract' || areas.length > 8 ? 'map' : 'chart',
      include_figure: true,
    });
    renderExportResult(res);
  } catch (e) {
    toast(`Export failed: ${e.message}`, 8000);
  } finally {
    updateActions();
  }
}

/**
 * The export result, as something a reader can click.
 *
 * A path in a message that disappears after five seconds is not a delivery: it
 * asks the reader to open a terminal. This panel stays until dismissed and
 * links every file the export wrote.
 */
function renderExportResult(res) {
  const m = currentMeasure();
  const n = res.selection.area_count;
  $('export-title').textContent = 'Export complete';
  $('export-what').textContent =
    `${m.label} — ${res.rows.toLocaleString('en-US')} row${res.rows === 1 ? '' : 's'} ` +
    `over ${n.toLocaleString('en-US')} ${levelNoun(n)}, ${state.catalog.period_label}.` +
    (res.data_mode === 'fixture'
      ? ' FIXTURE MODE: these values are synthetic test data, not census findings.'
      : '');
  const list = $('export-files');
  list.textContent = '';
  (res.artifacts || []).forEach((f) => {
    const li = document.createElement('li');
    const open = document.createElement('a');
    open.href = f.url;
    open.target = '_blank';
    open.rel = 'noopener';
    open.textContent = f.name;
    const what = document.createElement('span');
    what.className = 'f-what';
    what.textContent = f.label.includes('—') ? f.label.split('—').slice(1).join('—').trim()
      : f.label;
    const save = document.createElement('a');
    save.href = `${f.url}?download=1`;
    save.className = 'f-save';
    save.setAttribute('download', f.name);
    save.textContent = 'save a copy';
    li.append(open, what, save);
    list.appendChild(li);
  });
  $('export-where').textContent =
    `Written to ${res.export_dir} in this repository. This bundle is a ` +
    'generated snapshot, not a saved project: nothing re-checks its inputs ' +
    'later. Save the view first if you want that check on reopening.';
  $('export-result').hidden = false;
  $('export-result').scrollIntoView({ block: 'nearest' });
}

/* -------------------------------------------------------------- projects */

async function saveProject(event) {
  event.preventDefault();
  if (!state.ready) {
    toast('Nothing is saved until a selection has finished loading.');
    return;
  }
  const id = $('project-id').value.trim();
  if (!id) { toast('Give the view a short name first.'); return; }
  try {
    const res = await post('/api/projects', {
      project_id: id,
      title: `${currentMeasure().label} — ${state.catalog.period_label}`,
      release_id: state.releaseId,
      measure_id: state.measureId,
      level: state.level,
      areas: scopedGeoids(),
      question_id: state.questionId,
      benchmark_id: state.benchmarkId,
    });
    toast(`Saved “${res.saved}”, pinning ${res.pinned_inputs} input files. Reopening ` +
      'checks them and refuses rather than showing different numbers.', 7000);
    $('project-id').value = '';
    renderProjects();
  } catch (e) { toast(`Could not save: ${e.message}`, 8000); }
}

async function renderProjects() {
  const list = $('project-list');
  list.textContent = '';
  let items = [];
  try { items = (await api('/api/projects')).projects; } catch { /* optional */ }
  if (!items.length) {
    list.innerHTML = '<li class="muted tiny">Nothing saved yet.</li>';
    return;
  }
  items.forEach((p) => {
    const li = document.createElement('li');
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'btn ghost p-name';
    open.innerHTML = `${esc(p.project_id)}<span class="p-q">${esc(p.measure_id || 'saved view')}</span>`;
    open.addEventListener('click', () => reopen(p.project_id));
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn ghost';
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
  // Two reopens in quick succession, or a reopen racing a control change,
  // must not interleave: only the newest one is allowed to apply.
  let replay = null;
  const outcome = await projectGate.run(
    () => api(`/api/project?id=${encodeURIComponent(projectId)}`),
    {
      commit: (r) => { replay = r; },
      fail: (e) => {
        if (e.kind === 'pin_mismatch') {
          showBlocked(`“${projectId}” was not reopened`, [
            e.message,
            'Nothing was substituted and nothing was re-fetched.',
          ]);
          toast(`“${projectId}” was not reopened: its inputs changed since it ` +
            'was saved.', 9000);
        } else {
          toast(`Could not reopen ${projectId}: ${e.message}`, 8000);
        }
      },
    });
  if (outcome.outcome !== 'committed' || !replay) return;

  const p = replay.project;
  const brief = replay.brief || {};
  state.releaseId = p.release_id;
  state.level = p.level;
  state.measureId = p.measure_id;
  state.areas = p.areas || [];
  state.benchmarkId = brief.benchmark_id || 'none';
  state.pick = null;
  state.compare = [];
  state.placeQuery = '';
  $('place-search').value = '';
  state.measureFilter = '';
  $('measure-search').value = '';
  // Any load started before this project was applied now describes a
  // selection nobody asked for.
  dataGate.invalidate();
  benchGate.invalidate();
  fitMap();
  renderLevelSwitch();
  renderSidebar();
  await loadBenchmarks();
  await refresh();
  toggleSavePanel(false);
  const n = replay.pin?.input_count ?? 0;
  toast(`Reopened “${projectId}”. All ${n} pinned input files were present and ` +
    'unchanged, so this is the view that was saved.', 7000);
}

function renderFooter() {
  const ref = state.status.annotation_reference || {};
  // The published copy is a set of files, not a running service, and saying
  // otherwise would be the footer's own first inaccuracy.
  const closing = STATIC
    ? 'This is a published copy: a set of files built from that manifest. It ' +
      'holds no credentials and requests nothing outside this site. Its own ' +
      'source manifest is at data/manifest.json.'
    : 'This service holds no credentials and makes no network requests.';
  $('footer-provenance').textContent =
    `Data mode: ${state.dataset.data_mode}. Built ${state.dataset.built_at} from manifest ` +
    `${state.dataset.manifest_id} at code revision ${state.dataset.code_revision}. ` +
    `Annotation semantics from ${ref.source_url || 'n/a'} (retrieved ${ref.retrieved_at || 'n/a'}). ` +
    closing;
}

boot().catch((e) => {
  $('startup').innerHTML =
    `<p class="blocked"><strong>Could not start</strong> ${esc(e.message)}</p>` +
    '<p><a href="./">Open the current home page</a></p>';
});
