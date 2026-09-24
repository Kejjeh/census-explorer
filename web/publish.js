/* Pure presentation helpers for the published snapshot; no survey arithmetic. */
'use strict';
// The scope rules live in core.js, loaded first by the page; under Node the
// tests load this file on its own.
const SHARE_SCOPE_RULES = (typeof module === 'object' && module.exports)
  ? require('./core.js') : { parseScope, resolveScope };
/* Version 1 links predate scopes and always meant New York City. Version 2
 * carries the scope. Neither may name a place in its view that is outside
 * that scope; the inspected place and the comparison may lie outside it and
 * the page labels them when they do. */
const SHARE_KEYS = {
  1: ['v', 'snapshot', 'release', 'level', 'measure', 'areas', 'benchmark', 'pick', 'compare'],
  2: ['v', 'snapshot', 'release', 'level', 'scope', 'measure', 'areas', 'benchmark', 'pick', 'compare'],
};
function validateSharedView(view, context) {
  const fail = (message) => { throw new Error(`Shared view: ${message}`); };
  if (!view || typeof view !== 'object' || Array.isArray(view)) fail('invalid link.');
  const keys = SHARE_KEYS[view.v];
  if (!keys) fail('unsupported link version.');
  if (Object.keys(view).some(k => !keys.includes(k)) || keys.some(k => !(k in view))) fail('unsupported link format.');
  if (view.snapshot !== context.snapshot) fail('it was made from a different published snapshot of the data.');
  if (view.release !== context.release) fail('this release is not available here.');
  const level = context.catalog.levels[view.level];
  if (!level || !level.groups) fail('unknown geography level.');
  if (!level.groups.flatMap(g => g.measures).some(m => m.measure_id === view.measure)) fail('unknown measure for this level.');
  const ids = new Set(context.areas.filter(a => a.level === view.level).map(a => a.geoid));
  // A build that predates scopes has no borough list: its whole level is the view.
  let inScope = ids;
  let scope = null;
  if (Array.isArray(context.catalog.boroughs)) {
    if (view.v === 2 && typeof view.scope !== 'string') fail('unknown scope.');
    try {
      scope = SHARE_SCOPE_RULES.parseScope(view.v === 1 ? 'nyc' : view.scope);
      inScope = new Set(SHARE_SCOPE_RULES.resolveScope(scope, view.level, context.areas,
        context.catalog.boroughs, Boolean(context.catalog.statewide)));
    } catch (e) { fail(`this scope cannot be shown here (${e.message}).`); }
  } else if (view.v === 2 && view.scope !== 'nyc') {
    fail('this copy has no scopes other than New York City.');
  }
  const validList = (list, max, allowed) => Array.isArray(list) && list.length <= max &&
    new Set(list).size === list.length && list.every(g => typeof g === 'string' && allowed.has(g));
  if (!validList(view.areas, inScope.size, inScope)) fail('unknown, duplicate, or out-of-scope places.');
  if (!validList(view.compare, 2, ids)) fail('unknown, duplicate, or incompatible places.');
  if (view.pick !== null && !ids.has(view.pick)) fail('unknown inspected place.');
  const references = ['none', 'nyc'];
  if (context.catalog.statewide) references.push('nys');
  if (view.level === 'tract' && scope) {
    const counties = new Set((view.areas.length ? view.areas : [...inScope]).map(g => g.slice(0, 5)));
    if (scope.kind === 'county' || counties.size === 1) references.push('containing_county', 'containing_borough');
  }
  if (!references.includes(view.benchmark)) fail('this reference is not supported on the published site.');
  return view;
}
function encodeSharedView(view, context) {
  validateSharedView(view, context);
  const hash = '#view=' + encodeURIComponent(JSON.stringify(view));
  if (hash.length > 12000) throw new Error('Too many selected places for a shareable link. Download the CSV instead.');
  return hash;
}
function decodeSharedView(hash, context) {
  if (!hash.startsWith('#view')) return null;
  if (!hash.startsWith('#view=')) throw new Error('Shared view: the link is incomplete or damaged.');
  if (hash.length > 12000) throw new Error('Shared view: link is too long.');
  let view;
  try { view = JSON.parse(decodeURIComponent(hash.slice(6))); }
  catch (_) { throw new Error('Shared view: the link is incomplete or damaged.'); }
  return validateSharedView(view, context);
}
function publishedBrief({dataset, measure, areas, values, quality, benchmark, snapshot, compare = [], coverage = [], scopeLabel = ''}) {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const names = Object.fromEntries(dataset.areas.map(a => [a.geoid, a.name]));
  const number = n => Number.isFinite(n) ? n.toLocaleString('en-US', {maximumFractionDigits: measure.unit === 'percent' ? 1 : 0, minimumFractionDigits: measure.unit === 'percent' ? 1 : 0}) : 'unavailable';
  const estimate = v => v.es === 'ok' ? number(v.e) + (measure.unit === 'percent' ? '%' : '') : 'No data — ' + (v.er || 'estimate unavailable');
  const uncertainty = v => v.es !== 'ok' ? 'Estimate unavailable' : v.ctl ? 'No sampling error (controlled total)' :
    v.ms === 'ok' && Number.isFinite(v.m) ? '± ' + number(v.m) + (measure.unit === 'percent' ? ' percentage points' : ' people') + ' (90% confidence)' :
    'Margin of error unavailable — ' + (v.mr || 'no usable margin published');
  const row = g => { const v = values[g] || {}; return `<tr><th scope="row">${esc(names[g] || g)}<small>${esc(g)}</small></th><td>${esc(estimate(v))}</td><td>${esc(uncertainty(v))}</td><td>${esc(v.n == null ? '—' : Math.round(v.n).toLocaleString('en-US'))}</td><td>${esc(v.d == null ? '—' : Math.round(v.d).toLocaleString('en-US'))}</td></tr>`; };
  const table = ids => '<table><thead><tr><th>Place</th><th>Estimate</th><th>Uncertainty</th><th>Numerator (people)</th><th>Denominator (people)</th></tr></thead><tbody>' + ids.map(row).join('') + '</tbody></table>';
  // Bound the printable summary, explicitly. CSV continues to contain every selected row.
  const shown = [...areas].sort().slice(0, 25);
  const b = benchmark;
  const reference = !b ? 'No reference selected.' : !b.available ? `${b.label}: unavailable — ${b.unavailable_reason || 'no usable reference'}` :
    `${b.label}: ${estimate({es: b.estimate_status || (b.estimate == null ? 'unavailable' : 'ok'), e: b.estimate, er: b.estimate_reason})}; ${uncertainty({es:'ok', ctl:b.controlled, ms:b.moe_status, m:b.moe, mr:b.moe_reason})}. ${b.basis || ''}`;
  const release = dataset.release;
  const notes = [...Object.values(quality || {}).flatMap(q => q.lines || []), ...coverage];
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(measure.label)} — Census Explorer brief</title><style>
body{font:15px/1.5 system-ui,sans-serif;color:#172c40;max-width:1050px;margin:40px auto;padding:0 24px}h1{font-size:30px;line-height:1.2}h2{font-size:19px;margin-top:28px}p,li{overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;font-size:12px;table-layout:fixed}th,td{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid #ccd5dd;overflow-wrap:anywhere}th:first-child{width:24%}small{display:block;font-weight:400}thead{display:table-header-group}tr{break-inside:avoid}.notice{border-left:4px solid #236799;padding:12px;background:#f0f5fa}code{overflow-wrap:anywhere;font-size:11px} @page{size:A4;margin:16mm} @media print{body{margin:0;padding:0;font-size:11px}.screen{display:none}h2{break-after:avoid}table{font-size:9px}th,td{padding:6px}}
</style></head><body><p>CENSUS EXPLORER · PLACE BRIEF</p><h1>${esc(measure.label)}</h1><p>${esc(release.period_label)} · ${esc(release.product_label)}</p>
<p class="screen">Use your browser’s Print command to print this brief or save it as PDF.</p>
${dataset.data_mode !== 'live' ? '<p class="notice">FIXTURE DATA — synthetic test values, not census findings.</p>' : ''}
<p class="notice">Generated from the published snapshot, not a saved project. This document does not re-check raw inputs. ${scopeLabel ? 'View: ' + esc(scopeLabel) + '. ' : ''}${areas.length} selected ${areas.length === 1 ? 'place' : 'places'}; ${shown.length === areas.length ? 'all are listed below' : 'the first 25 in GEOID order are listed below; download CSV for all selected places'}. No combined estimate is implied.</p>
<h2>Definition and denominator</h2><p>${esc(measure.definition_note)}</p><p>Universe: ${esc(measure.universe_note)}. ${measure.unit === 'percent' ? 'Denominator: ' + esc(measure.out_of || measure.universe_note) : 'This is a count, not a percentage.'}</p>
<h2>Selected places</h2>${table(shown)}
${compare.length ? '<h2>Places chosen for side-by-side comparison</h2><p>These inspection choices do not change the selected scope above' + (compare.some(g => !areas.includes(g)) ? ', and ' + compare.filter(g => !areas.includes(g)).length + ' of them lie outside it' : '') + '.</p>' + table(compare) : ''}
<h2>Reference and limits</h2><p>${esc(reference)}</p><p>Differences between estimates are not tested for statistical significance. Read both margins of error. Missing uncertainty is unavailable, not zero. This period estimate does not describe any single year.</p><ul>${notes.map(n => '<li>' + esc(n) + '</li>').join('')}</ul>
<h2>Sources and reproducibility</h2><p>${esc(release.citation)}</p><p>Geography: ${esc(release.geography_vintage)}. Tables: ${esc((measure.tables || []).join(', '))}.</p><p>Numerator cells: ${esc((measure.numerator_cells || []).join(' + ') || 'not applicable')}. Denominator cells: ${esc((measure.denominator_cells || []).join(' + ') || 'not applicable')}.</p><p>Published snapshot: <code>${esc(snapshot)}</code></p><p>Release: ${esc(release.release_id)}. Data built: ${esc(dataset.built_at)}. Data manifest: ${esc(dataset.manifest_id)}.</p></body></html>`;
}
if (typeof module === 'object' && module.exports) module.exports = {encodeSharedView, decodeSharedView, publishedBrief};
