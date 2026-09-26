/*
 * Hospital layer: NYSDOH HFIS hospital sites and CMS reporting entities.
 *
 * Optional and off by default. Nothing is requested until the reader turns
 * the layer on, and turning it off removes every marker, so the census view
 * underneath is exactly what it was.
 *
 * Two identifier systems are shown side by side and never merged: an HFIS
 * site (fac_id) and a CMS reporting entity (CCN). The registry lists CCN ->
 * site *candidates* from an address rule; it never calls one a match, and a
 * CMS rating is shown only on the CMS entity it belongs to.
 *
 * Every string from a source is escaped before it reaches the page, and the
 * only links are built from fixed official addresses.
 */
/* global document */

const HOSP_PAGE = 25;
const HOSP_SVG = 'http://www.w3.org/2000/svg';
const HOSP_LINK_HOSTS = ['data.cms.gov', 'health.data.ny.gov', 'data.ny.gov',
  'profiles.health.ny.gov', 'www.medicare.gov'];
const CCN_PATTERN = /^[0-9A-Z]{6}$/;

//: HFIS facility types in two groups. A hospital can itself list another
//: hospital as its main site (a division); that relationship is shown
//: separately and is never inferred from the type.
const GROUP_LABEL = {
  hospital: 'Hospital',
  extension: 'Hospital extension site',
};

const LOCATION_SHORT = {
  missing: 'Not mapped: no location published',
  incomplete: 'Not mapped: incomplete coordinates',
  unparseable: 'Not mapped: coordinates not numbers',
  zero: 'Not mapped: placeholder 0, 0',
  outside_new_york: 'Not mapped: coordinates outside New York',
};

/** A short, controlled label for the directory's Location column. */
function mapLabel(s) {
  if (s.map_status === 'mapped') return 'Mapped';
  if (s.map_status === 'not_mapped_county_conflict') {
    return `Not mapped: published point is in ${s.location_in_county_name || 'another'} County, not the listed ${s.county_name}`;
  }
  if (s.map_status === 'not_mapped_county_unverified') return 'Not mapped: point not placed in one county';
  return LOCATION_SHORT[s.location_status] || 'Not mapped: no location';
}

const MAIN_SITE_TEXT = {
  is_main_site: 'HFIS lists no other main site for this hospital.',
  self: 'HFIS lists this site as its own main site.',
  listed: 'HFIS lists this main site:',
  not_in_registry: 'HFIS lists a main site that is not a hospital-family site in this registry:',
  not_listed: 'HFIS lists no main site for this site.',
};

const MATCH_TEXT = {
  candidate: 'Candidate (unreviewed)',
  ambiguous: 'Ambiguous',
  unresolved: 'Unresolved',
};

function hEsc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** An https address on one of the official hosts, or null. */
function safeOfficialUrl(url) {
  try {
    const u = new URL(String(url));
    return u.protocol === 'https:' && HOSP_LINK_HOSTS.includes(u.hostname) ? u.href : null;
  } catch (_) {
    return null;
  }
}

function careCompareUrl(registry, ccn) {
  if (!CCN_PATTERN.test(String(ccn || ''))) return null;
  return safeOfficialUrl(registry.links.care_compare_prefix + encodeURIComponent(ccn));
}

function byCountyThenName(a, b) {
  return (a.county_name || '').localeCompare(b.county_name || '')
    || (a.name || '').localeCompare(b.name || '')
    || a.fac_id.localeCompare(b.fac_id);
}

/** Sites that pass every filter, in a stable order. */
function filterSites(sites, f) {
  const q = (f.q || '').trim().toLowerCase();
  return sites.filter((s) => {
    if (f.county && (s.county_fips || 'unmatched') !== f.county) return false;
    if (f.type === 'group:hospital' && s.type_group !== 'hospital') return false;
    if (f.type === 'group:extension' && s.type_group !== 'extension') return false;
    if (f.type && !f.type.startsWith('group:') && s.type !== f.type) return false;
    if (f.owner && s.ownership !== f.owner) return false;
    if (q && ![s.name, s.fac_id, s.city, s.address1, s.main_site_name]
      .some((x) => String(x || '').toLowerCase().includes(q))) return false;
    return true;
  }).sort(byCountyThenName);
}

function countBy(list, key, label) {
  const m = new Map();
  list.forEach((x) => {
    const k = key(x);
    const cur = m.get(k) || { value: k, label: label(x), count: 0 };
    cur.count += 1;
    m.set(k, cur);
  });
  return [...m.values()].sort((a, b) => a.label.localeCompare(b.label));
}

function facets(sites) {
  return {
    counties: countBy(sites, (s) => s.county_fips || 'unmatched',
      (s) => s.county_fips ? s.county_name : `${s.county_name_hfis} (no FIPS)`),
    types: countBy(sites, (s) => s.type, (s) => s.type),
    owners: countBy(sites, (s) => s.ownership, (s) => s.ownership),
  };
}

/**
 * What the map can show of a filtered list.
 *
 * Contract: filters use the county HFIS lists. A site is drawn only if its
 * registry map_status is "mapped" (its published point, NYSDOH's geocode of
 * the mailing address, lies in that listed county) and that county is in the
 * mapped area. A site whose point lies in another county is counted as a
 * conflict and listed, never drawn under the county it is filtered by.
 * `scopeCounties` null means the whole state.
 */
function mapPlan(filtered, scopeCounties) {
  const inScope = scopeCounties ? new Set(scopeCounties) : null;
  const plan = { drawn: [], unlocated: 0, conflict: 0, outside: 0 };
  filtered.forEach((s) => {
    if (s.map_status === 'not_mapped_no_location') plan.unlocated += 1;
    else if (s.map_status !== 'mapped') plan.conflict += 1;
    else if (inScope && !inScope.has(s.county_fips)) plan.outside += 1;
    else plan.drawn.push(s);
  });
  return plan;
}

/** The plan in words; the directory summary and the map note both use it. */
function planSentence(plan) {
  const n = (x) => x.toLocaleString('en-US');
  return [
    `${n(plan.drawn.length)} are drawn on the map`,
    plan.outside ? `${n(plan.outside)} lie in counties outside the mapped area` : '',
    plan.conflict ? `${n(plan.conflict)} are not drawn because the published point lies outside the listed county` : '',
    plan.unlocated ? `${n(plan.unlocated)} have no published location` : '',
  ].filter(Boolean).join('; ') + '. Points are NYSDOH geocodes of each site\'s mailing address, as published.';
}

const SITE_CSV_COLUMNS = [
  'hfis_fac_id', 'name', 'type', 'type_group', 'main_site_fac_id', 'main_site_name',
  'main_site_status', 'address1', 'address2', 'city', 'zip', 'hfis_county_code',
  'hfis_county_name', 'county_fips', 'county_crosswalk', 'ownership', 'ownership_types',
  'operators', 'latitude', 'longitude', 'location_status', 'location_reason',
  'location_county_check', 'point_county_fips', 'point_county_name', 'map_status', 'map_reason',
  'certified_bed_records_json', 'certification_rows',
  'cms_ccn_candidates', 'open_date_as_published', 'hfis_rows_updated',
  'data_mode', 'retrieval_manifest_id', 'rules_version', 'rules_sha256',
];

/**
 * Registry and certification documents are only used together if they come
 * from the same build: same schema, retrieval, rules and data mode. Unknown
 * modes are refused rather than shown.
 */
function checkPair(registry, cert) {
  if (!registry || registry.schema_version !== 2) throw new Error('The hospital registry has an unsupported schema.');
  if (!['live', 'fixture'].includes(registry.data_mode)) {
    throw new Error(`The hospital registry has an unknown data mode (${registry.data_mode}); it is not shown.`);
  }
  if (!cert) return;
  for (const key of ['schema_version', 'retrieval_manifest_id', 'rules_sha256', 'data_mode']) {
    if (cert[key] !== registry[key]) {
      throw new Error(`The hospital certification file does not belong to this registry (${key} differs); neither is shown.`);
    }
  }
}

/** Words that must accompany any hospital record that is not live data. */
function modeWarning(registry) {
  return registry.data_mode === 'live' ? ''
    : 'SYNTHETIC HOSPITAL DATA (fixture mode). These hospital records are test data, not NYSDOH or CMS records, whatever the census data on this page is.';
}

/** A CSV cell. Text that a spreadsheet would run as a formula is prefixed
 *  with an apostrophe; plain numbers (negative longitudes) are left alone. */
function hospCsvField(value) {
  let text = value === null || value === undefined ? '' : String(value);
  if (/^[=+\-@\t\r]/.test(text) && !/^-?\d+(\.\d+)?$/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function sitesCsv(list, registry) {
  const updated = (registry.dates && registry.dates.nys) || '';
  const rows = [SITE_CSV_COLUMNS.join(',')];
  list.forEach((s) => {
    rows.push([
      s.fac_id, s.name, s.type, s.type_group, s.main_site_fac_id, s.main_site_name,
      s.main_site_status, s.address1, s.address2, s.city, s.zip, s.hfis_county_code,
      s.county_name_hfis, s.county_fips, s.county_crosswalk, s.ownership,
      (s.ownership_types || []).join('; '),
      (s.operators || []).map((o) => o.name).join('; '),
      s.lat, s.lon, s.location_status, s.location_reason, s.location_county_check,
      s.location_in_county_fips, s.location_in_county_name, s.map_status, s.map_reason,
      // Every bed record whole, as JSON: category, count or why there is none,
      // sub type, raw and parsed date, date note. Never a total.
      JSON.stringify(s.certified_beds || []),
      s.certification_rows,
      (s.cms_candidates || []).map((c) => `${c.ccn} (${c.state})`).join('; '),
      s.open_date_raw, updated, registry.data_mode, registry.retrieval_manifest_id,
      registry.rules_version, registry.rules_sha256,
    ].map(hospCsvField).join(','));
  });
  return rows.join('\r\n') + '\r\n';
}

/** The overall rating in words, with the reason when there is none. */
function ratingSummary(entity) {
  const notes = (entity.overall_rating_footnotes || []).map((n) => (
    n.text ? `Footnote ${n.code}: ${n.text}` : `Footnote ${n.code} (not in the CMS footnote list)`));
  if (entity.overall_rating_status === 'rated') {
    return { short: `${entity.overall_rating} of 5`, notes };
  }
  return {
    short: 'Not available',
    notes: notes.length ? notes : ['CMS gives no footnote for this.'],
  };
}

/* ----------------------------------------------------------- the widget */

function createHospitalLayer() {
  const hs = {
    deps: null, on: false, reg: null, cert: null, loading: null, error: null,
    view: 'sites', f: { q: '', county: '', type: '', owner: '' },
    page: 0, cmsPage: 0, pick: null, pickCms: null, mapCtx: null, k: 1,
    filtered: [], sitesById: new Map(), entsByCcn: new Map(),
  };
  const $h = (id) => document.getElementById(id);

  function init(deps) {
    hs.deps = deps;
    const row = $h('hosp-toggle-row');
    if (!row) return;
    if (!deps.available) {
      row.hidden = true;
      return;
    }
    row.hidden = false;
    $h('hosp-toggle').addEventListener('change', (e) => setOn(e.target.checked));
  }

  async function load() {
    if (hs.reg) return;
    if (!hs.loading) {
      hs.loading = hs.deps.api('/api/hospitals').then((reg) => {
        checkPair(reg, null);
        hs.reg = reg;
        reg.sites.forEach((s) => hs.sitesById.set(s.fac_id, s));
        reg.cms_entities.forEach((e) => hs.entsByCcn.set(e.ccn, e));
      }).finally(() => { hs.loading = null; });
    }
    await hs.loading;
  }

  async function certification() {
    if (!hs.cert) {
      const cert = await hs.deps.api('/api/hospitals/certification');
      checkPair(hs.reg, cert);
      hs.cert = cert;
    }
    return hs.cert;
  }

  async function setOn(on) {
    hs.on = on;
    const section = $h('hospitals');
    if (!on) {
      section.hidden = true;
      $h('hosp-map-note').textContent = '';
      drawMarkers();
      return;
    }
    section.hidden = false;
    section.innerHTML = '<p class="muted">Loading the hospital registry…</p>';
    try {
      await load();
      hs.error = null;
    } catch (e) {
      hs.error = e.message || String(e);
    }
    if (!hs.on) return;
    renderSection();
    drawMarkers();
  }

  /* ---- map ---- */

  function scopeCounties() {
    const c = hs.deps.scopeCounties();
    return c && c.length ? c : null;
  }

  function draw(ctx) {
    hs.mapCtx = ctx;
    drawMarkers();
  }

  function drawMarkers() {
    const ctx = hs.mapCtx;
    if (!ctx || !ctx.layer) return;
    const old = ctx.layer.querySelector('#hosp-layer');
    if (old) old.remove();
    if (!hs.on || !hs.reg) return;
    const plan = mapPlan(hs.filtered, scopeCounties());
    const g = document.createElementNS(HOSP_SVG, 'g');
    g.id = 'hosp-layer';
    // Hospitals on top of extension sites.
    const ordered = [...plan.drawn].sort((a, b) => (a.type_group === 'hospital') - (b.type_group === 'hospital'));
    ordered.forEach((s) => {
      const [x, y] = ctx.project(s.lon, s.lat);
      const main = s.type_group === 'hospital';
      const el = document.createElementNS(HOSP_SVG, main ? 'circle' : 'rect');
      el.setAttribute('class', main ? 'hosp-mark main' : 'hosp-mark operated');
      el.dataset.fac = s.fac_id;
      el.dataset.x = String(x);
      el.dataset.y = String(y);
      if (s.fac_id === hs.pick) el.dataset.pick = 'true';
      const t = document.createElementNS(HOSP_SVG, 'title');
      t.textContent = `${s.name} (${GROUP_LABEL[s.type_group]}, HFIS ${s.fac_id})`;
      el.appendChild(t);
      g.appendChild(el);
    });
    ctx.layer.appendChild(g);
    sizeMarkers();
    renderSummary();
    const warn = modeWarning(hs.reg);
    $h('hosp-map-note').textContent = (warn ? `${warn} ` : '') +
      `Of ${hs.filtered.length.toLocaleString('en-US')} filtered hospital sites, ${planSentence(plan)} ` +
      'Circles are hospitals; squares are hospital extension sites (clinics, school-based ' +
      'and mobile sites, off-campus emergency departments). Sites not drawn are listed below with the reason.';
  }

  function zoom(k) {
    hs.k = k || 1;
    sizeMarkers();
  }

  function sizeMarkers() {
    const ctx = hs.mapCtx;
    const g = ctx && ctx.layer && ctx.layer.querySelector('#hosp-layer');
    if (!g) return;
    const k = hs.k;
    g.querySelectorAll('.hosp-mark').forEach((el) => {
      const x = Number(el.dataset.x); const y = Number(el.dataset.y);
      const big = el.dataset.pick === 'true' ? 1.6 : 1;
      if (el.tagName === 'circle') {
        el.setAttribute('cx', x); el.setAttribute('cy', y);
        el.setAttribute('r', (4.2 * big) / k);
      } else {
        const h = (3 * big) / k;
        el.setAttribute('x', x - h); el.setAttribute('y', y - h);
        el.setAttribute('width', 2 * h); el.setAttribute('height', 2 * h);
      }
      el.setAttribute('stroke-width', 1.1 / k);
    });
  }

  /* ---- directory ---- */

  function applyFilters() {
    hs.filtered = filterSites(hs.reg.sites, hs.f);
    if (hs.page * HOSP_PAGE >= hs.filtered.length) hs.page = 0;
  }

  function renderSection() {
    const section = $h('hospitals');
    if (hs.error) {
      section.innerHTML = `<h2 id="hosp-title">Hospitals</h2><p class="blocked">${hEsc(hs.error)}</p>`;
      return;
    }
    const reg = hs.reg;
    applyFilters();
    const fc = facets(reg.sites);
    const opt = (list, cur) => list.map((o) => (
      `<option value="${hEsc(o.value)}"${o.value === cur ? ' selected' : ''}>` +
      `${hEsc(o.label)} (${o.count.toLocaleString('en-US')})</option>`)).join('');
    const c = reg.reports.counts;
    const warn = modeWarning(reg);
    section.innerHTML = `
      ${warn ? `<p class="hosp-fixture" role="alert">${hEsc(warn)}</p>` : ''}
      <div class="hosp-head">
        <h2 id="hosp-title" tabindex="-1">Hospitals${warn ? ' (synthetic test data)' : ''}</h2>
        <p class="muted tiny">${hEsc(reg.dates.nys)} ${hEsc(reg.dates.cms)} ${hEsc(reg.dates.acs)}</p>
      </div>
      <div class="hosp-tabs" role="group" aria-label="Directory">
        <button type="button" class="chip" id="hosp-tab-sites" aria-pressed="${hs.view === 'sites'}">HFIS sites (${c.hospital_family_sites.toLocaleString('en-US')})</button>
        <button type="button" class="chip" id="hosp-tab-cms" aria-pressed="${hs.view === 'cms'}">CMS reporting entities (${c.cms_ny_rows})</button>
      </div>
      <div id="hosp-body"></div>
      <div id="hosp-detail" class="hosp-detail" role="region" aria-label="Hospital details" tabindex="-1"></div>
      <details class="hosp-coverage"><summary>Coverage, identifiers and checks</summary>${coverageHtml(reg)}</details>`;
    $h('hosp-tab-sites').addEventListener('click', () => switchView('sites'));
    $h('hosp-tab-cms').addEventListener('click', () => switchView('cms'));
    if (hs.view === 'sites') renderSitesView(fc, opt);
    else renderCmsView();
    renderDetail();
  }

  function switchView(view) {
    hs.view = view;
    renderSection();
    $h(view === 'sites' ? 'hosp-tab-sites' : 'hosp-tab-cms').focus();
  }

  function renderSitesView(fc, opt) {
    const body = $h('hosp-body');
    const typeOpts = [
      { value: 'group:hospital', label: 'All hospitals', count: hs.reg.reports.counts.hospital_sites },
      { value: 'group:extension', label: 'All hospital extension sites', count: hs.reg.reports.counts.extension_sites },
      ...fc.types,
    ];
    body.innerHTML = `
      <div class="hosp-filters">
        <label class="field"><span>Search name, city or HFIS ID</span>
          <input type="search" id="hosp-q" autocomplete="off" value="${hEsc(hs.f.q)}"></label>
        <label class="field"><span>County</span><select id="hosp-county">
          <option value="">All counties</option>${opt(fc.counties, hs.f.county)}</select></label>
        <label class="field"><span>Type</span><select id="hosp-type">
          <option value="">All types</option>${opt(typeOpts, hs.f.type)}</select></label>
        <label class="field"><span>Ownership (as listed by HFIS)</span><select id="hosp-owner">
          <option value="">All ownership types</option>${opt(fc.owners, hs.f.owner)}</select></label>
      </div>
      <p class="hosp-summary" id="hosp-summary" aria-live="polite"></p>
      <div class="table-scroll"><table class="hosp-table" id="hosp-table">
        <caption class="visually-hidden">Hospital-family sites from NYSDOH HFIS, filtered as above.</caption>
        <thead><tr><th scope="col">Site</th><th scope="col">Type</th><th scope="col">County</th>
        <th scope="col">Location</th><th scope="col">CMS entity candidate</th></tr></thead>
        <tbody id="hosp-rows"></tbody></table></div>
      <nav class="table-pager" id="hosp-pager" aria-label="Hospital directory pages"></nav>
      <p><button type="button" class="btn" id="hosp-csv"></button></p>`;
    const onFilter = (key) => (e) => {
      hs.f[key] = e.target.value; hs.page = 0;
      applyFilters(); renderRows(); drawMarkers();
    };
    $h('hosp-q').addEventListener('input', onFilter('q'));
    $h('hosp-county').addEventListener('change', onFilter('county'));
    $h('hosp-type').addEventListener('change', onFilter('type'));
    $h('hosp-owner').addEventListener('change', onFilter('owner'));
    $h('hosp-csv').addEventListener('click', downloadCsv);
    renderRows();
  }

  function renderSummary() {
    const box = $h('hosp-summary');
    if (!box) return;
    const list = hs.filtered;
    const plan = mapPlan(list, scopeCounties());
    box.textContent =
      `${list.length.toLocaleString('en-US')} of ${hs.reg.sites.length.toLocaleString('en-US')} sites match ` +
      `(county = the county HFIS lists). ${planSentence(plan)}`;
  }

  function renderRows() {
    const list = hs.filtered;
    renderSummary();
    const start = hs.page * HOSP_PAGE;
    const rows = list.slice(start, start + HOSP_PAGE);
    $h('hosp-rows').innerHTML = rows.map((s) => `
      <tr${s.fac_id === hs.pick ? ' class="picked"' : ''}>
        <th scope="row"><button type="button" class="linkish" data-fac="${hEsc(s.fac_id)}">${hEsc(s.name)}</button>
          <span class="muted tiny">HFIS ${hEsc(s.fac_id)} · ${hEsc(s.city)}</span></th>
        <td data-label="Type">${hEsc(s.type)}${s.type !== GROUP_LABEL[s.type_group] ? `<br><span class="muted tiny">${hEsc(GROUP_LABEL[s.type_group])}</span>` : ''}</td>
        <td data-label="County">${hEsc(s.county_name)}</td>
        <td data-label="Location"${s.map_status === 'mapped' ? '' : ' class="hosp-unmapped"'}>${hEsc(mapLabel(s))}</td>
        <td data-label="CMS entity candidate">${(s.cms_candidates || []).map((c) => `${hEsc(c.ccn)} <span class="muted tiny">${hEsc(MATCH_TEXT[c.state])}</span>`).join('<br>') || '<span class="muted">none</span>'}</td>
      </tr>`).join('') || '<tr><td colspan="5">No site matches these filters.</td></tr>';
    $h('hosp-rows').querySelectorAll('button[data-fac]').forEach((b) => {
      b.addEventListener('click', () => openSite(b.dataset.fac, { focus: true }));
    });
    renderPager('hosp-pager', list.length, hs.page, (p) => { hs.page = p; renderRows(); });
    $h('hosp-csv').textContent = `Download these ${list.length.toLocaleString('en-US')} sites (CSV)`;
  }

  function renderPager(id, total, page, go) {
    const pages = Math.max(1, Math.ceil(total / HOSP_PAGE));
    const nav = $h(id);
    if (pages === 1) { nav.innerHTML = ''; return; }
    nav.innerHTML = `
      <button type="button" class="btn" data-dir="-1"${page === 0 ? ' disabled' : ''}>Previous</button>
      <span class="muted tiny">Page ${page + 1} of ${pages}</span>
      <button type="button" class="btn" data-dir="1"${page >= pages - 1 ? ' disabled' : ''}>Next</button>`;
    nav.querySelectorAll('button[data-dir]').forEach((b) => b.addEventListener('click', () => {
      const dir = b.dataset.dir;
      go(page + Number(dir));
      // Keep focus on the pager: the same button if it is still usable.
      const fresh = $h(id);
      const same = fresh.querySelector(`button[data-dir="${dir}"]`);
      (same && !same.disabled ? same : fresh.querySelector('button:not([disabled])')).focus();
    }));
  }

  function renderCmsView() {
    const ents = hs.reg.cms_entities;
    const start = hs.cmsPage * HOSP_PAGE;
    $h('hosp-body').innerHTML = `
      <p class="muted tiny">${hEsc(hs.reg.definitions.ccn)} ${hEsc(hs.reg.definitions.overall_rating)}
        Ratings are shown as CMS publishes them: no averages, rankings or recommendations.</p>
      <div class="table-scroll"><table class="hosp-table" id="hosp-cms-table">
        <caption class="visually-hidden">CMS Hospital General Information, New York rows.</caption>
        <thead><tr><th scope="col">Reporting entity</th><th scope="col">Hospital type</th>
        <th scope="col">Overall rating</th><th scope="col">HFIS site candidate</th></tr></thead>
        <tbody>${ents.slice(start, start + HOSP_PAGE).map((e) => `
          <tr${e.ccn === hs.pickCms ? ' class="picked"' : ''}>
            <th scope="row"><button type="button" class="linkish" data-ccn="${hEsc(e.ccn)}">${hEsc(e.name)}</button>
              <span class="muted tiny">CCN ${hEsc(e.ccn)} · ${hEsc(e.city)}</span></th>
            <td data-label="Hospital type">${hEsc(e.hospital_type)}</td>
            <td data-label="Overall rating">${hEsc(ratingSummary(e).short)}</td>
            <td data-label="HFIS site candidate">${hEsc(MATCH_TEXT[e.hfis_match_state])}</td></tr>`).join('')}</tbody></table></div>
      <nav class="table-pager" id="hosp-cms-pager" aria-label="CMS entity pages"></nav>`;
    $h('hosp-body').querySelectorAll('button[data-ccn]').forEach((b) => {
      b.addEventListener('click', () => openEntity(b.dataset.ccn, { focus: true }));
    });
    renderPager('hosp-cms-pager', ents.length, hs.cmsPage, (p) => { hs.cmsPage = p; renderCmsView(); });
  }

  /* ---- details ---- */

  function openSite(facId, opts = {}) {
    if (!hs.reg || !hs.sitesById.has(facId)) return;
    hs.pick = facId; hs.pickCms = null;
    if (hs.view !== 'sites') { hs.view = 'sites'; renderSection(); } else { renderRows(); renderDetail(); }
    drawMarkers();
    if (opts.focus) $h('hosp-detail').focus();
    else $h('hosp-detail').scrollIntoView({ block: 'nearest' });
  }

  function openEntity(ccn, opts = {}) {
    if (!hs.reg || !hs.entsByCcn.has(ccn)) return;
    hs.pickCms = ccn; hs.pick = null;
    if (hs.view !== 'cms') { hs.view = 'cms'; renderSection(); } else { renderCmsView(); renderDetail(); }
    drawMarkers();
    if (opts.focus) $h('hosp-detail').focus();
  }

  function siteButton(facId, label) {
    return `<button type="button" class="linkish" data-open-fac="${hEsc(facId)}">${hEsc(label)}</button>`;
  }

  function wireDetailButtons(el) {
    el.querySelectorAll('button[data-open-fac]').forEach((b) => b.addEventListener('click',
      () => openSite(b.dataset.openFac, { focus: true })));
    el.querySelectorAll('button[data-open-ccn]').forEach((b) => b.addEventListener('click',
      () => openEntity(b.dataset.openCcn, { focus: true })));
  }

  function link(url, text) {
    const safe = safeOfficialUrl(url);
    return safe ? `<a href="${hEsc(safe)}" target="_blank" rel="noopener noreferrer">${hEsc(text)}</a>` : '';
  }

  function renderDetail() {
    const el = $h('hosp-detail');
    if (!el) return;
    if (hs.pick) el.innerHTML = siteDetailHtml(hs.sitesById.get(hs.pick));
    else if (hs.pickCms) el.innerHTML = entityDetailHtml(hs.entsByCcn.get(hs.pickCms));
    else {
      el.innerHTML = '<p class="muted">Choose a site or an entity to see its details.</p>';
      return;
    }
    wireDetailButtons(el);
    if (hs.pick) fillCertification(hs.pick);
  }

  function siteDetailHtml(s) {
    const reg = hs.reg;
    const main = s.main_site_status === 'listed' || s.main_site_status === 'not_in_registry'
      ? `${hEsc(MAIN_SITE_TEXT[s.main_site_status])} ` + (s.main_site_status === 'listed'
        ? siteButton(s.main_site_fac_id, `${s.main_site_name} (HFIS ${s.main_site_fac_id})`)
        : `${hEsc(s.main_site_name)} (HFIS ${hEsc(s.main_site_fac_id)})`)
      : hEsc(MAIN_SITE_TEXT[s.main_site_status]);
    const operated = reg.sites.filter((x) => x.main_site_status === 'listed' && x.main_site_fac_id === s.fac_id);
    const opList = operated.length
      ? `<p>${operated.length.toLocaleString('en-US')} site${operated.length === 1 ? '' : 's'} list this as their main site: ` +
        operated.slice(0, 8).map((x) => siteButton(x.fac_id, x.name)).join(', ') +
        (operated.length > 8 ? ` and ${operated.length - 8} more (search the directory for them).` : '.') + '</p>'
      : '';
    const countyNote = s.county_crosswalk === 'reviewed_alias'
      ? ` HFIS spells it “${hEsc(s.county_name_hfis)}”; matched to the Census name by a reviewed spelling alias.` : '';
    const localityNote = s.locality_check !== 'agrees'
      ? ` The NYS Locality Hierarchy gives ${hEsc(s.locality_fips || 'no code')} here (${hEsc(s.locality_check.replace(/_/g, ' '))}); the Census code is used.` : '';
    const cands = (s.cms_candidates || []).map((c) => (
      `<li><button type="button" class="linkish" data-open-ccn="${hEsc(c.ccn)}">CCN ${hEsc(c.ccn)}</button>: ` +
      `${hEsc(MATCH_TEXT[c.state])}</li>`)).join('');
    return `
      <h3>${hEsc(s.name)}</h3>
      <p>${hEsc(s.type)}${s.type !== GROUP_LABEL[s.type_group] ? ` · <strong>${hEsc(GROUP_LABEL[s.type_group])}</strong>` : ''}</p>
      <dl class="hosp-dl">
        <dt>HFIS facility ID</dt><dd>${hEsc(s.fac_id)} <span class="muted tiny">${hEsc(reg.definitions.fac_id)}</span></dd>
        <dt>Address</dt><dd>${hEsc(s.address1)}${s.address2 ? `, ${hEsc(s.address2)}` : ''}, ${hEsc(s.city)}, NY ${hEsc(s.zip)}</dd>
        <dt>County</dt><dd>${hEsc(s.county_name)} (FIPS ${hEsc(s.county_fips || 'not matched')}; HFIS county code ${hEsc(s.hfis_county_code)}, which is not a FIPS code).${countyNote}${localityNote}</dd>
        <dt>Location</dt><dd>${s.location_status === 'valid'
          ? `Published point ${hEsc(s.lat)}, ${hEsc(s.lon)} (NYSDOH geocode of the mailing address). ` : ''}${hEsc(s.map_reason)}${s.map_status === 'mapped' ? '' : ' The site stays in the directory and the CSV.'}</dd>
        <dt>Main site</dt><dd>${main}</dd>
        <dt>Ownership</dt><dd>${hEsc(s.ownership)}${(s.ownership_types || []).length > 1 ? ` (${hEsc(s.ownership_types.join('; '))})` : ''}</dd>
        <dt>Operator</dt><dd>${hEsc((s.operators || []).map((o) => o.name).join('; '))}</dd>
        <dt>Operating certificate</dt><dd>${hEsc(s.operating_certificate || 'not listed')}</dd>
      </dl>
      ${opList}
      <h4>Operating certificate: beds and services</h4>
      <div id="hosp-cert">${s.certification_rows ? '<p class="muted">Loading…</p>' : '<p>HFIS lists no certification rows for this site.</p>'}</div>
      <h4>CMS reporting entity</h4>
      ${cands ? `<p class="muted tiny">Candidates from one rule: ${hEsc(reg.definitions.match_rule)} None is a confirmed match. A CMS entity's rating belongs to the entity, not to this site.</p><ul>${cands}</ul>`
        : '<p>No CMS reporting entity lists this address. That does not mean the site has no Medicare certification.</p>'}
      <h4>Sources</h4>
      <ul class="hosp-links">
        <li>${link(reg.sources.nys_general.landing_page, 'NYSDOH Health Facility General Information')} (${hEsc(reg.dates.nys)})</li>
        <li>${link(reg.links.nys_profiles_listing, 'NYS Health Profiles: hospitals A–Z')} <span class="muted tiny">${hEsc(reg.links.nys_profiles_note)}</span></li>
      </ul>`;
  }

  async function fillCertification(facId) {
    const s = hs.sitesById.get(facId);
    if (!s || !s.certification_rows) return;
    let rows;
    try {
      rows = ((await certification()).sites || {})[facId] || [];
    } catch (e) {
      const box = $h('hosp-cert');
      if (box && hs.pick === facId) box.innerHTML = `<p class="blocked">${hEsc(e.message)}</p>`;
      return;
    }
    const box = $h('hosp-cert');
    if (!box || hs.pick !== facId) return;
    const date = (r) => (r.effective_date_note === 'conversion_default'
      ? `${hEsc(r.effective_date)} <span class="muted tiny">(system-conversion date; the true date is not available)</span>`
      : r.effective_date_note === 'unparseable'
        ? `${hEsc(r.effective_date_raw || 'blank')} <span class="muted tiny">(not a date as published)</span>`
        : hEsc(r.effective_date));
    const beds = rows.filter((r) => r.attribute_type === 'Bed');
    const services = rows.filter((r) => r.attribute_type !== 'Bed');
    box.innerHTML = `
      <p class="muted tiny">${hEsc(hs.reg.definitions.beds)}</p>
      ${beds.length ? `<table class="hosp-mini"><caption class="visually-hidden">Certified bed records, one per operating-certificate row, not added up</caption>
        <thead><tr><th scope="col">Bed category</th><th scope="col">Certified beds</th><th scope="col">Sub type</th><th scope="col">Effective</th></tr></thead>
        <tbody>${beds.map((r) => `<tr><td>${hEsc(r.attribute_value)}</td><td>${r.certified_beds === null || r.certified_beds === undefined ? `<span class="muted">${hEsc(r.count_note || 'not a count')} (“${hEsc(r.measure_value_raw)}”)</span>` : hEsc(r.certified_beds)}</td><td>${hEsc(r.sub_type || 'blank')}</td><td>${date(r)}</td></tr>`).join('')}</tbody></table>`
        : '<p>No certified bed categories are listed for this site.</p>'}
      <details><summary>${services.length} listed service${services.length === 1 ? '' : 's'}</summary>
        <p class="muted tiny">${hEsc(hs.reg.definitions.services)}</p>
        <ul class="hosp-services">${services.map((r) => `<li>${hEsc(r.attribute_value)}${r.sub_type ? ` <span class="muted tiny">(${hEsc(r.sub_type)})</span>` : ''} · effective ${date(r)}</li>`).join('')}</ul>
      </details>`;
  }

  function entityDetailHtml(e) {
    const reg = hs.reg;
    const rating = ratingSummary(e);
    const cc = careCompareUrl(reg, e.ccn);
    const cands = e.hfis_candidates.map((c) => `
      <tr><td>${siteButton(c.fac_id, c.name)}<br><span class="muted tiny">HFIS ${hEsc(c.fac_id)} · ${hEsc(GROUP_LABEL[c.type_group])}</span></td>
      <td>${hEsc(c.address1)}, ${hEsc(c.zip)}</td><td>${hEsc(c.normalized_address)}</td>
      <td>${hEsc(c.name_token_overlap)}</td></tr>`).join('');
    return `
      <h3>${hEsc(e.name)}</h3>
      <p>CMS reporting entity · CCN ${hEsc(e.ccn)} · ${hEsc(e.hospital_type)}</p>
      <dl class="hosp-dl">
        <dt>Address (CMS)</dt><dd>${hEsc(e.address)}, ${hEsc(e.city)}, NY ${hEsc(e.zip)} (${hEsc(e.county_parish)})</dd>
        <dt>Ownership (CMS)</dt><dd>${hEsc(e.ownership)}</dd>
        <dt>Emergency services</dt><dd>${hEsc(e.emergency_services)}</dd>
        <dt>Birthing-friendly designation</dt><dd>${hEsc(e.birthing_friendly)}</dd>
        <dt>Overall star rating</dt><dd><strong>${hEsc(rating.short)}</strong>
          ${rating.notes.map((n) => `<br><span class="tiny">${hEsc(n)}</span>`).join('')}
          <br><span class="muted tiny">${hEsc(reg.definitions.overall_rating)} ${hEsc(reg.dates.cms)}</span></dd>
      </dl>
      <h4>HFIS site candidates: ${hEsc(MATCH_TEXT[e.hfis_match_state])}</h4>
      <p class="muted tiny">${hEsc(e.hfis_match_note)} Rule: ${hEsc(reg.definitions.match_rule)}</p>
      ${cands ? `<div class="table-scroll"><table class="hosp-mini"><caption class="visually-hidden">Evidence for each candidate</caption>
        <thead><tr><th scope="col">HFIS site</th><th scope="col">HFIS address</th><th scope="col">Normalized address (both)</th><th scope="col">Name word overlap (review aid only)</th></tr></thead>
        <tbody>${cands}</tbody></table></div>` : ''}
      <h4>Sources</h4>
      <ul class="hosp-links">
        <li>${cc ? link(cc, `Medicare Care Compare: CCN ${e.ccn}`) : 'No Care Compare link: the CCN is not in the expected form.'} <span class="muted tiny">${hEsc(reg.links.care_compare_note)}</span></li>
        <li>${link(reg.sources.cms_general.landing_page, 'CMS Hospital General Information')} (released ${hEsc(reg.sources.cms_general.released)})</li>
      </ul>`;
  }

  function coverageHtml(reg) {
    const r = reg.reports;
    const c = r.counts;
    const li = (obj) => Object.entries(obj).map(([k, v]) => `<li>${hEsc(k)}: ${hEsc(typeof v === 'object' ? JSON.stringify(v) : v)}</li>`).join('');
    return `
      <p>Retrieval ${hEsc(reg.retrieval_manifest_id)}. CMS: ${c.cms_rows.toLocaleString('en-US')} rows, ${c.cms_ny_rows} in New York.
        HFIS General: ${c.nys_general_rows.toLocaleString('en-US')} rows; ${c.hospital_family_rows.toLocaleString('en-US')} are hospital-family rows,
        which describe ${c.hospital_family_sites.toLocaleString('en-US')} sites (${c.hospital_sites} hospitals, of which ${c.hospitals_listing_another_main_site} list another hospital as their main site, and ${c.extension_sites.toLocaleString('en-US')} extension sites;
        ${r.duplicates.fac_ids_with_repeated_rows} sites appear on several rows, once per operator or cooperator).
        HFIS Certification: ${c.certification_rows.toLocaleString('en-US')} rows, ${c.certification_rows_for_sites.toLocaleString('en-US')} for these sites.</p>
      <p>Other HFIS facility types, not shown:</p><ul>${li(c.excluded_rows_by_type)}</ul>
      <p>Locations: ${hEsc(JSON.stringify(r.locations.by_status))}. Point-in-county check against Census shapes: ${hEsc(JSON.stringify(r.locations.county_shape_check))}. Map: ${hEsc(JSON.stringify(r.locations.by_map_status))}.</p>
      <p>${hEsc(r.locations.map_contract)}</p>
      <details><summary>${r.locations.in_other_county.length} sites whose published point lies outside their listed county</summary>
        <div class="table-scroll"><table class="hosp-mini"><caption class="visually-hidden">Sites whose published point lies outside the listed county</caption>
        <thead><tr><th scope="col">HFIS site</th><th scope="col">Listed county</th><th scope="col">County of published point</th></tr></thead>
        <tbody>${r.locations.in_other_county.map((x) => `<tr><td>${hEsc(x.name)} <span class="muted tiny">HFIS ${hEsc(x.fac_id)}</span></td><td>${hEsc(x.listed_county)} (${hEsc(x.listed_fips)})</td><td>${hEsc(x.point_county || 'none')} (${hEsc(x.point_fips || '—')})</td></tr>`).join('')}</tbody></table></div>
      </details>
      <p>Counties: FIPS from ${hEsc(r.counties.fips_source)}. Locality Hierarchy cross-check disagreements: ${hEsc(JSON.stringify(r.counties.locality_disagreements))}.</p>
      <p>CMS entity to HFIS site candidates, by state: ${hEsc(JSON.stringify(r.cms_match.by_state))}. Sites named for several CCNs: ${hEsc(JSON.stringify(r.cms_match.sites_shared_by_ccns))}.</p>
      <p>Reconciliation checks:</p><ul>${r.checks.map((k) => `<li>${k.passed ? 'Passed' : 'FAILED'}: ${hEsc(k.detail)}</li>`).join('')}</ul>
      <p class="muted tiny">No population is attributed to any site or tract; hospital data and census estimates are separate products with separate dates.</p>`;
  }

  function downloadCsv() {
    const text = sitesCsv(hs.filtered, hs.reg);
    const name = `hospital-sites-${hs.reg.data_mode === 'live' ? '' : 'SYNTHETIC-'}${hs.reg.retrieval_manifest_id}.csv`;
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  return {
    init, draw, zoom, openSite, openEntity, setOn,
    _state: hs,
  };
}

if (typeof module === 'object' && module.exports) {
  module.exports = {
    filterSites, facets, mapPlan, sitesCsv, hospCsvField, ratingSummary,
    safeOfficialUrl, careCompareUrl, hEsc, SITE_CSV_COLUMNS, checkPair, modeWarning,
    mapLabel, planSentence,
  };
} else if (typeof window !== 'undefined') {
  window.HospitalLayer = createHospitalLayer();
}
