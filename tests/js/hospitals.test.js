/* Hospital layer logic, on SYNTHETIC records (no real hospital). */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const H = require('../../web/hospitals.js');

const site = (id, extra = {}) => ({
  fac_id: id, name: `Synthetic ${id}`, type: 'Hospital', type_group: 'hospital',
  county_fips: '36001', county_name: 'Albany', county_name_hfis: 'Albany',
  ownership: 'County', city: 'Testville', address1: '1 Test St',
  location_status: 'valid', lat: 42.6, lon: -73.7, ...extra,
});

const SITES = [
  site('0101'),
  site('202', { type: 'Hospital Extension Clinic', type_group: 'extension',
    location_status: 'missing', lat: null, lon: null }),
  site('303', { county_fips: '36089', county_name: 'St. Lawrence', ownership: 'State' }),
  site('404', { county_fips: '36089', county_name: 'St. Lawrence', name: '=HYPERLINK("x")' }),
];

test('filters combine and never drop an unlocated site', () => {
  assert.deepStrictEqual(H.filterSites(SITES, { county: '36001' }).map((s) => s.fac_id), ['0101', '202']);
  assert.deepStrictEqual(H.filterSites(SITES, { type: 'group:extension' }).map((s) => s.fac_id), ['202']);
  assert.deepStrictEqual(H.filterSites(SITES, { owner: 'State' }).map((s) => s.fac_id), ['303']);
  assert.deepStrictEqual(H.filterSites(SITES, { q: '0101' }).map((s) => s.fac_id), ['0101']);
  assert.strictEqual(H.filterSites(SITES, {}).length, SITES.length);
});

test('map, table and CSV describe the same filtered records', () => {
  const filtered = H.filterSites(SITES, { county: '36001' });
  const plan = H.mapPlan(filtered, ['36001']);
  assert.deepStrictEqual(plan.drawn.map((s) => s.fac_id), ['0101']);
  assert.strictEqual(plan.unlocated, 1);
  const lines = H.sitesCsv(filtered, { retrieval_manifest_id: 'm', dates: { nys: 'd' } })
    .trim().split('\r\n');
  assert.strictEqual(lines.length, 1 + filtered.length);
  assert.strictEqual(lines[0], H.SITE_CSV_COLUMNS.join(','));
  assert.ok(lines[1].startsWith('0101,'), 'leading zero kept');
  assert.ok(lines[2].includes('missing'));
});

test('sites outside the mapped counties are counted, not drawn', () => {
  const plan = H.mapPlan(SITES, ['36001']);
  assert.strictEqual(plan.outside, 2);
  assert.strictEqual(H.mapPlan(SITES, null).drawn.length, 3);
});

test('CSV cells cannot run as spreadsheet formulas; negative numbers stay numbers', () => {
  assert.strictEqual(H.hospCsvField('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"');
  assert.strictEqual(H.hospCsvField('-73.75'), '-73.75');
  assert.strictEqual(H.hospCsvField('-x'), "'-x");
});

test('ratings: unavailable is a reason, never zero', () => {
  const r = H.ratingSummary({ overall_rating_status: 'not_available', overall_rating: null,
    overall_rating_footnotes: [{ code: '19', text: 'Shown only for IQR/OQR hospitals.' }] });
  assert.strictEqual(r.short, 'Not available');
  assert.deepStrictEqual(r.notes, ['Footnote 19: Shown only for IQR/OQR hospitals.']);
  const none = H.ratingSummary({ overall_rating_status: 'not_available', overall_rating_footnotes: [] });
  assert.deepStrictEqual(none.notes, ['CMS gives no footnote for this.']);
  assert.strictEqual(H.ratingSummary({ overall_rating_status: 'rated', overall_rating: 4,
    overall_rating_footnotes: [] }).short, '4 of 5');
});

test('links: only https on official hosts, CCN in the documented form', () => {
  const reg = { links: { care_compare_prefix: 'https://www.medicare.gov/care-compare/details/hospital/' } };
  assert.strictEqual(H.careCompareUrl(reg, '33009F'),
    'https://www.medicare.gov/care-compare/details/hospital/33009F');
  assert.strictEqual(H.careCompareUrl(reg, '33/../x'), null);
  assert.strictEqual(H.safeOfficialUrl('javascript:alert(1)'), null);
  assert.strictEqual(H.safeOfficialUrl('http://data.cms.gov/x'), null);
  assert.strictEqual(H.safeOfficialUrl('https://evil.example/x'), null);
});

test('escaping covers markup and quotes', () => {
  assert.strictEqual(H.hEsc(`<img src=x onerror="a">'`), '&lt;img src=x onerror=&quot;a&quot;&gt;&#39;');
});

test('facets list unmatched counties rather than hiding them', () => {
  const f = H.facets([...SITES, site('9', { county_fips: null, county_name_hfis: 'Nowhere', county_name: 'Nowhere' })]);
  assert.ok(f.counties.some((c) => c.value === 'unmatched' && c.label === 'Nowhere (no FIPS)'));
});
