
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {encodeSharedView, decodeSharedView, publishedBrief} = require('../../web/publish.js');
const {createStaticBackend} = require('../../web/static.js');
const context = {snapshot:'snapshot-a', release:'acs', catalog:{levels:{county:{groups:[{measures:[{measure_id:'share'}]}]}}}, areas:[{geoid:'001', level:'county'}, {geoid:'002', level:'county'}, {geoid:'003', level:'tract'}]};
const view = {v:1, snapshot:'snapshot-a', release:'acs', level:'county', measure:'share', areas:['001'], benchmark:'nyc', pick:'002', compare:['002','001']};
test('shared link restores scope, inspection, ordered comparison and reference exactly', () => {
  assert.deepEqual(decodeSharedView(encodeSharedView(view, context), context), view);
  assert.deepEqual(decodeSharedView(encodeSharedView({...view, areas:[]}, context), context).areas, []);
  assert.equal(decodeSharedView('#stage',context),null);
});
test('shared links reject changed snapshots and incompatible or malformed selections', () => {
  for (const change of [{snapshot:'old'}, {release:'other'}, {v:2}, {level:'tract'}, {measure:'unknown'}, {areas:['003']}, {areas:['001','001']}, {compare:['001','002','001']}, {pick:'not-real'}, {benchmark:'selected'}, {extra:'bad'}]) {
    assert.throws(() => decodeSharedView('#view='+encodeURIComponent(JSON.stringify({...view,...change})), context), /Shared view/);
  }
  assert.throws(() => decodeSharedView('#view',context), /damaged/);
  assert.throws(() => decodeSharedView('#view=%ZZ',context), /damaged/);
  assert.throws(() => decodeSharedView('#view='+ 'x'.repeat(12001),context), /too long/);
});
// A scoped build: two New York City counties stand in for the five, plus Erie.
const scoped = {snapshot:'snap', release:'acs', catalog:{boroughs:['36005','36047'], statewide:true,
  levels:{county:{groups:[{measures:[{measure_id:'share'}]}]}, tract:{groups:[{measures:[{measure_id:'share'}]}]}}},
  areas:[{geoid:'36005',level:'county'},{geoid:'36047',level:'county'},{geoid:'36029',level:'county'},
    {geoid:'36005000100',level:'tract'},{geoid:'36047000100',level:'tract'},{geoid:'36029016600',level:'tract'},{geoid:'36029990000',level:'tract'}]};
const v2 = {v:2, snapshot:'snap', release:'acs', level:'tract', scope:'county:36029', measure:'share', areas:[], benchmark:'containing_county', pick:'36005000100', compare:['36047000100','36029016600']};
const hashOf = (view) => '#view=' + encodeURIComponent(JSON.stringify(view));
test('a scoped link restores its county, and may inspect places outside it', () => {
  assert.deepEqual(decodeSharedView(encodeSharedView(v2, scoped), scoped), v2);
  assert.deepEqual(decodeSharedView(hashOf({...v2, scope:'nys', benchmark:'nys'}), scoped).scope, 'nys');
});
test('a link made before scopes is the city, never the state', () => {
  const v1 = {v:1, snapshot:'snap', release:'acs', level:'tract', measure:'share', areas:['36005000100'], benchmark:'nyc', pick:'36029016600', compare:[]};
  assert.deepEqual(decodeSharedView(hashOf(v1), scoped), v1);
  // An Erie tract was never in a version 1 view, so it cannot be named as one.
  assert.throws(() => decodeSharedView(hashOf({...v1, areas:['36029016600']}), scoped), /out-of-scope/);
  // The whole city spans several counties, so it has no single containing county.
  assert.throws(() => decodeSharedView(hashOf({...v1, areas:[], benchmark:'containing_county'}), scoped), /not supported/);
  assert.equal(decodeSharedView(hashOf({...v1, benchmark:'containing_county'}), scoped).benchmark, 'containing_county');
});
test('a scoped link cannot list places outside its scope or ask for an impossible reference', () => {
  for (const change of [{areas:['36005000100']}, {scope:'county:36999'}, {scope:'county:36029', level:'county', measure:'share'},
    {scope:'borough:36005'}, {scope:'nys', benchmark:'containing_county'}, {scope:undefined}]) {
    const view = {...v2, ...change};
    if (change.scope === undefined && 'scope' in change) delete view.scope;
    assert.throws(() => decodeSharedView(hashOf(view), scoped), /Shared view/, JSON.stringify(change));
  }
  const narrow = {...scoped, catalog:{...scoped.catalog, statewide:false}};
  assert.throws(() => decodeSharedView(hashOf({...v2, scope:'nys', benchmark:'none'}), narrow), /cannot be shown/);
  assert.throws(() => decodeSharedView(hashOf({...v2, scope:'nyc', benchmark:'nys', areas:[], compare:[], pick:null}), narrow), /not supported/);
});
const {selectionForLevel} = require('../../web/core.js');
test('county to tract: a county inspected or compared does not follow into the tract view', () => {
  // The reviewed failure: New York State counties, Erie County inspected and
  // compared, then "Explore this county's census tracts".
  const before = {pick:'36029', compare:['36029', '36005']};
  const stale = {v:2, snapshot:'snap', release:'acs', level:'tract', scope:'county:36029', measure:'share',
    areas:[], benchmark:'none', pick:before.pick, compare:before.compare};
  // Exactly the reported message when only the inspected place is stale...
  assert.throws(() => encodeSharedView({...stale, compare:[]}, scoped), /unknown inspected place/);
  // ...and a stale comparison is refused too.
  assert.throws(() => encodeSharedView({...stale, pick:null}, scoped), /incompatible places/);
  const kept = selectionForLevel(before, 'tract', scoped.areas);
  assert.deepEqual(kept, {pick:null, compare:[]});
  const view = {...stale, ...kept};
  assert.deepEqual(decodeSharedView(encodeSharedView(view, scoped), scoped), view);
});
test('a same-level place outside the scope stays inspected and comparable across a scope change', () => {
  const before = {pick:'36005000100', compare:['36005000100', '36029016600']};
  assert.deepEqual(selectionForLevel(before, 'tract', scoped.areas), before);
  const view = {...v2, benchmark:'none', ...before};
  assert.deepEqual(decodeSharedView(encodeSharedView(view, scoped), scoped), view);
  // Going back up to counties keeps nothing from the tract level.
  assert.deepEqual(selectionForLevel(before, 'county', scoped.areas), {pick:null, compare:[]});
});
const input = {dataset:{data_mode:'live', release:{period_label:'2019–2023 ACS', citation:'Census Bureau'}, areas:[{geoid:'001',name:'Bronx <script>alert(1)</script>'}]}, measure:{label:'Naturalized share',unit:'percent',out_of:'foreign-born people', tables:['B05002'], numerator_cells:['N'],denominator_cells:['D']}, areas:['001'],values:{'001':{e:53.7,es:'ok',m:0.8,ms:'ok',n:537,d:1000}}, quality:{comparison:{lines:['Within one period.']}}, benchmark:null,snapshot:'snapshot-a'};
test('brief keeps denominator, percentage-point MOE, scope and escaped source content', () => {
  const html=publishedBrief(input);
  for(const text of ['53.7%', '0.8 percentage points','foreign-born people','537','1,000','1 selected place','snapshot-a','not a saved project','Census Bureau']) assert.ok(html.includes(text), text);
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
});
test('brief retains missing estimate, unavailable reference and controlled uncertainty', () => {
  const missing=publishedBrief({...input,values:{'001':{es:'missing',er:'suppressed'}},benchmark:{available:false,label:'NYC',unavailable_reason:'incomplete borough membership'}});
  assert.ok(missing.includes('No data — suppressed'));
  assert.ok(missing.includes('incomplete borough membership'));
  const controlled=publishedBrief({...input, values:{'001':{e:0,es:'ok',ctl:true}},benchmark:{available:true,label:'NYC',estimate:0,controlled:true}});
  assert.ok(controlled.includes('0.0%'));
  assert.equal((controlled.match(/No sampling error \(controlled total\)/g)||[]).length,2);
  const noMoe=publishedBrief({...input, values:{'001':{e:53.7,es:'ok',ms:'unavailable',mr:'too few sample cases'}}});
  assert.ok(noMoe.includes('too few sample cases'));
});
test('large brief explicitly lists only 25 rows without claiming an aggregate', () => {
  const areas=Array.from({length:26},(_,i)=>String(i).padStart(3,'0'));
  const html=publishedBrief({...input,areas});
  assert.ok(html.includes('26 selected places'));
  assert.ok(html.includes('first 25 in GEOID order'));
  assert.ok(html.includes('No combined estimate is implied'));
  assert.equal((html.match(/<tr>/g)||[]).length,26);
});
test('digest checking says why it cannot run without a secure origin', async () => {
  // crypto.subtle is absent over plain http. Without this the first data
  // fetch failed on "cannot read properties of undefined", which tells a
  // reader nothing about the cause.
  const fetchOriginal = global.fetch;
  const cryptoOriginal = global.crypto;
  try {
    global.fetch = async () => ({ok: true, arrayBuffer: async () => new ArrayBuffer(2)});
    Object.defineProperty(global, 'crypto', {value: {}, configurable: true});
    const backend = createStaticBackend({dataDigests: {'data/status.json': '0'.repeat(64)}});
    await assert.rejects(backend.get('/api/status', new URLSearchParams()),
                         /secure origin/);
  } finally {
    global.fetch = fetchOriginal;
    Object.defineProperty(global, 'crypto', {value: cryptoOriginal, configurable: true});
  }
});
test('a file outside the published snapshot is refused', async () => {
  const original = global.fetch;
  try {
    global.fetch = async () => ({ok: true, arrayBuffer: async () => new ArrayBuffer(2)});
    const backend = createStaticBackend({dataDigests: {'data/other.json': '0'.repeat(64)}});
    await assert.rejects(backend.get('/api/status', new URLSearchParams()),
                         /not part of the published snapshot/);
  } finally { global.fetch = original; }
});
test('published files fail closed on digest mismatch and can retry', async () => {
  const original=global.fetch;
  try {
    const bytes=new TextEncoder().encode('{"releases":[]}');
    global.fetch=async()=>({ok:true,arrayBuffer:async()=>bytes.buffer});
    const backend=createStaticBackend({dataDigests:{'data/status.json':'0'.repeat(64)}});
    await assert.rejects(backend.get('/api/status',new URLSearchParams()),/changed or did not load/);
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('');
    const good=createStaticBackend({dataDigests:{'data/status.json':hash}});
    assert.deepEqual(await good.get('/api/status',new URLSearchParams()),{releases:[]});
  } finally {global.fetch=original;}
});
