/* The published site must say the same things the local service says.
 *
 * `web/static.js` answers the page's requests out of files a build wrote. It
 * does no survey arithmetic, but it does assemble the uncertainty panel from
 * counts and it does serialise the CSV, and either could drift away from the
 * Python that produced them.
 *
 * This test is driven by tests/test_site_build.py, which builds a fixture
 * site, runs `server.quality_report` and `exports.build_csv_for` for a list
 * of selections, and points CENSUS_EXPLORER_ROUNDTRIP at the result. Every
 * case here is then recomputed through static.js and compared.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const bundle = process.env.CENSUS_EXPLORER_ROUNDTRIP;
const { decodeValues, staticUncertainty, comparisonCase, buildCsv } =
  require('../../web/static.js');

function readJson(...parts) {
  return JSON.parse(fs.readFileSync(path.join(...parts), 'utf8'));
}

test('the published site reproduces the service, case by case', (t) => {
  assert.ok(bundle, 'CENSUS_EXPLORER_ROUNDTRIP is not set');
  const expected = readJson(bundle, 'expected.json');
  const site = path.join(bundle, 'site', 'data');
  const geoids = readJson(site, 'areas.json').geoids;
  const dataset = readJson(site, 'dataset.json');
  const catalog = readJson(site, 'catalog.json');

  const names = {};
  const levels = {};
  dataset.areas.forEach((a) => { names[a.geoid] = a.name; levels[a.geoid] = a.level; });

  expected.cases.forEach((c) => {
    const encoded = readJson(site, 'values', `${c.measure_id}.json`);
    const values = decodeValues(encoded, geoids);
    const cases = readJson(site, 'quality', `${c.level}.json`);
    const measure = catalog.levels[c.level].groups
      .flatMap((g) => g.measures).find((m) => m.measure_id === c.measure_id);

    const uncertainty = staticUncertainty(
      cases.uncertainty_templates, c.areas, values, measure.unit);
    assert.deepStrictEqual(uncertainty, c.expected_uncertainty,
      `uncertainty differs for ${c.name}`);

    const comparison = cases.comparison[comparisonCase(c.areas.length, c.benchmark)];
    assert.deepStrictEqual(comparison, c.expected_comparison,
      `comparison differs for ${c.name}`);

    assert.deepStrictEqual(cases.freshness, c.expected_freshness,
      `period panel differs for ${c.name}`);

    const csv = buildCsv({
      release: dataset.release,
      measure: dataset.measures.find((m) => m.measure_id === c.measure_id),
      areas: c.areas, areaNames: names, areaLevels: levels, values,
    });
    assert.strictEqual(csv, c.expected_csv, `CSV differs for ${c.name}`);
  });
});

test('decoding restores every field the service returned', (t) => {
  assert.ok(bundle, 'CENSUS_EXPLORER_ROUNDTRIP is not set');
  const expected = readJson(bundle, 'expected.json');
  const site = path.join(bundle, 'site', 'data');
  const geoids = readJson(site, 'areas.json').geoids;
  // A key the service returned as null and a key the encoding left out mean
  // the same thing — no value — so the comparison is made without them.
  const withoutNulls = (records) => Object.fromEntries(
    Object.entries(records).map(([geoid, record]) => [
      geoid,
      Object.fromEntries(Object.entries(record).filter(([, v]) => v !== null)),
    ]));
  Object.entries(expected.values).forEach(([measureId, records]) => {
    const decoded = decodeValues(
      readJson(site, 'values', `${measureId}.json`), geoids);
    assert.deepStrictEqual(decoded, withoutNulls(records),
      `decoded values differ from the service for ${measureId}`);
  });
});
