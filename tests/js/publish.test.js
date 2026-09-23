
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
