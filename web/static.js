/* Census Explorer — the static build's transport.
 *
 * A published site has no Python behind it, so this file answers the same
 * requests the local service answers, out of files written by
 * `census_explorer.site` during the build.
 *
 * It does no survey arithmetic. Every estimate, margin of error, coefficient
 * of variation, reliability judgement, denominator and reference value it
 * hands back was computed by the Python code and written out; what happens
 * here is reshaping, counting and text assembly. Where a feature genuinely
 * needs the service — writing an export bundle, saved
 * views and their input checks — it is reported as unavailable with a reason
 * rather than approximated.
 */
'use strict';

// The scope rules live in core.js, which the page loads first. Under Node the
// tests load this file on its own, so it asks for them directly.
const SCOPE_RULES = (typeof module === 'object' && module.exports)
  ? require('./core.js')
  : { parseScope, resolveScope, describeScope };

/* ------------------------------------------------------------- decoding */

/**
 * Rebuild `{geoid: record}` from the columnar form the build writes.
 *
 * The build stores one shared area order and, per measure, parallel columns
 * with repeated status words replaced by indices into a table. Nothing is
 * derived here: a field absent from the encoding was absent from the record.
 */
function decodeValues(encoded, geoids) {
  const notes = encoded.notes || [];
  const numeric = encoded.numeric || {};
  const text = encoded.text || {};
  const ctl = encoded.ctl || null;
  const flags = encoded.flags || null;
  const out = {};
  for (let i = 0; i < geoids.length; i += 1) {
    const record = {};
    Object.keys(numeric).forEach((key) => {
      const v = numeric[key][i];
      if (v !== null && v !== undefined) record[key] = v;
    });
    Object.keys(text).forEach((key) => {
      const idx = text[key][i];
      if (idx !== null && idx !== undefined) record[key] = notes[idx];
    });
    if (ctl && ctl[i] === 1) record.ctl = true;
    if (flags && flags[i] && flags[i].length) record.flags = flags[i];
    out[geoids[i]] = record;
  }
  return out;
}

/* -------------------------------------------------------------- quality */

/**
 * The uncertainty panel for one selection.
 *
 * The sentences come from the build; the counts are counted here over values
 * the build already computed. A round-trip test compares this against
 * `server.quality_report` for the real release, so the two cannot drift
 * apart unnoticed.
 */
function staticUncertainty(templates, areas, values, unit) {
  const usable = areas.filter((g) => (values[g] || {}).es === 'ok');
  const missing = areas.filter((g) => !((values[g] || {}).es === 'ok'));
  const withMoe = usable.filter((g) => values[g].ms === 'ok');
  const high = withMoe.filter((g) => values[g].cv !== null
    && values[g].cv !== undefined && values[g].cv >= 30);

  const lines = [];
  let word;
  let cls;
  if (!areas.length) {
    word = 'No areas selected'; cls = 'blocked';
  } else if (!usable.length) {
    word = 'No usable estimates'; cls = 'blocked';
    lines.push(templates.none_usable);
  } else {
    const shareHigh = high.length / usable.length;
    if (missing.length || shareHigh > 0.25) { word = 'Read with care'; cls = 'caution'; }
    else { word = 'Usable as published'; cls = 'ok'; }
    lines.push(
      templates.published
        .replace('{usable}', usable.length).replace('{total}', areas.length)
      + (missing.length
        ? templates.published_missing.replace('{missing}', missing.length)
        : templates.published_all));
    if (withMoe.length < usable.length) {
      lines.push(templates.no_moe.replace('{n}', usable.length - withMoe.length));
    }
    if (unit === 'persons' && high.length) {
      lines.push(templates.high_cv.replace('{n}', high.length));
    } else if (unit === 'percent') {
      let widest = null;
      withMoe.forEach((g) => {
        const m = values[g].m;
        if (m === null || m === undefined) return;
        if (widest === null || m > widest) widest = m;
      });
      if (widest !== null) {
        lines.push(templates.widest.replace('{widest}', widest.toFixed(1)));
      }
    }
  }
  return { state: word, class: cls, lines };
}

/** Which prewritten comparison paragraph this selection calls for. */
function comparisonCase(areaCount, benchmark) {
  if (areaCount > 1) return 'many';
  if (benchmark && benchmark.available) return 'one_with_reference';
  if (benchmark) return 'one_reference_unavailable';
  return 'one_no_reference';
}

/* ------------------------------------------------------------------ CSV */

//: Exactly the columns `census_explorer/exports.py` writes, in its order.
const CSV_COLUMNS = [
  'release_id', 'period_label', 'product', 'geography_vintage', 'boundary_release',
  'level', 'geoid', 'area_name', 'measure_id', 'measure_label', 'unit', 'kind',
  'estimate', 'estimate_status', 'estimate_note', 'moe_90pct', 'moe_status',
  'moe_note', 'cv_percent', 'reliability', 'numerator', 'denominator', 'universe',
  'numerator_cells', 'denominator_cells', 'source_flags',
];

/**
 * Render a number the way Python's csv writer renders the float it came from.
 *
 * Every numeric field in a value record is a Python float, and `str()` on an
 * integral float keeps the trailing `.0`. JSON turns that into a JavaScript
 * number, which would print without it, so a whole-number count would differ
 * from the CSV the local service writes for the same selection.
 */
function csvNumber(value) {
  if (value === null || value === undefined) return '';
  if (typeof value !== 'number') return String(value);
  return Number.isInteger(value) ? value.toFixed(1) : String(value);
}

function csvField(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/**
 * The CSV for one selection, with the same columns and the same values as the
 * bundle the local service exports.
 */
function buildCsv({ release, measure, areas, areaNames, areaLevels, values }) {
  const rows = [CSV_COLUMNS.join(',')];
  [...areas].sort().forEach((geoid) => {
    const v = values[geoid];
    if (!v) return;
    rows.push([
      release.release_id, release.period_label, release.product_label,
      release.geography_vintage, release.boundary_release,
      areaLevels[geoid], geoid, areaNames[geoid],
      measure.measure_id, measure.label, measure.unit, measure.kind,
      csvNumber(v.e), v.es || '', v.er || '',
      csvNumber(v.m), v.ms || '', v.mr || '',
      csvNumber(v.cv), v.rel || '',
      csvNumber(v.n), csvNumber(v.d),
      measure.universe_note,
      (measure.numerator_cells || []).join(';'),
      (measure.denominator_cells || []).join(';'),
      (v.flags || []).join(';'),
    ].map(csvField).join(','));
  });
  return rows.join('\n') + '\n';
}

/* ------------------------------------------------------------- transport */

/**
 * Answers the endpoints the page asks for, out of the build's files.
 *
 * Files are fetched once and kept: a published dataset does not change while
 * someone is reading it, and re-fetching a 2,327-area layer on every control
 * change would make the map feel broken.
 */
function createStaticBackend(config) {
  const base = config.base || 'data/';
  const cache = new Map();
  let areaOrder = null;

  async function file(name) {
    if (!cache.has(name)) {
      cache.set(name, fetch(base + name, { cache: 'no-cache', headers: { Accept: 'application/json' } })
        .then(async (res) => {
          if (!res.ok) {
            const err = new Error(
              `this published site is missing ${name} (${res.status}). It may ` +
              'have been deployed from an incomplete build.');
            err.status = res.status;
            throw err;
          }
          if (config.dataDigests) {
            const expected = config.dataDigests['data/' + name];
            if (!expected) throw new Error('This file is not part of the published snapshot.');
            // Browsers expose crypto.subtle only on a secure origin. Without
            // this the first data fetch fails on "cannot read properties of
            // undefined", which says nothing about the cause.
            const subtle = (typeof crypto !== 'undefined' && crypto.subtle) || null;
            if (!subtle) {
              throw new Error(
                'This copy checks every published file against its digest, ' +
                'which the browser allows only on a secure origin. Open this ' +
                'site over https.');
            }
            const bytes = await res.arrayBuffer();
            const hash = Array.from(new Uint8Array(await subtle.digest('SHA-256', bytes)))
              .map(b => b.toString(16).padStart(2, '0')).join('');
            if (hash !== expected) throw new Error('Published files changed or did not load consistently. Reload the page before sharing or exporting.');
            return JSON.parse(new TextDecoder().decode(bytes));
          }
          return res.json();
        })
        .catch((e) => { cache.delete(name); throw e; }));
    }
    return cache.get(name);
  }

  async function order() {
    if (!areaOrder) areaOrder = (await file('areas.json')).geoids;
    return areaOrder;
  }

  async function valuesFor(measureId) {
    const key = `values:${measureId}`;
    if (!cache.has(key)) {
      cache.set(key, (async () => {
        const [encoded, geoids] = await Promise.all([
          file(`values/${measureId}.json`), order(),
        ]);
        return { meta: encoded, values: decodeValues(encoded, geoids) };
      })().catch((e) => { cache.delete(key); throw e; }));
    }
    return cache.get(key);
  }

  function param(query, name, fallback) {
    const v = query.get(name);
    return v === null || v === '' ? fallback : v;
  }

  /**
   * The places a request covers, resolved exactly as the service resolves
   * them. A request with no scope is New York City, so a link made before the
   * build covered the state still shows the city and nothing more.
   */
  function scopeOf(query, dataset, catalog, level) {
    const scope = SCOPE_RULES.parseScope(query.get('scope'));
    const boroughs = catalog.boroughs || [];
    const statewide = Boolean(catalog.statewide);
    const geoids = SCOPE_RULES.resolveScope(scope, level, dataset.areas, boroughs, statewide);
    const counties = new Set(dataset.areas.filter((a) => a.level === 'county').map((a) => a.geoid));
    const partial = scope.kind === 'nyc' && boroughs.some((g) => !counties.has(g));
    let countyName = null;
    if (scope.county) {
      const c = dataset.areas.find((a) => a.geoid === scope.county);
      countyName = c ? c.name : scope.county;
      if (boroughs.includes(scope.county)) countyName += ', a New York City borough';
    }
    return {
      scope, geoids,
      label: SCOPE_RULES.describeScope(scope, level, geoids.length, countyName, partial),
    };
  }

  function selectedAreas(query, dataset, catalog, level) {
    const within = scopeOf(query, dataset, catalog, level);
    const raw = query.get('areas');
    if (!raw) return { areas: within.geoids, scope: within };
    const requested = raw.split(',').filter(Boolean);
    const inView = new Set(within.geoids);
    const atLevel = new Set(dataset.areas.filter((a) => a.level === level).map((a) => a.geoid));
    const outside = requested.filter((g) => atLevel.has(g) && !inView.has(g));
    if (outside.length) {
      throw new Error(`${outside.length} requested place(s) are outside the scope ` +
        `'${within.scope.code}': ${outside.slice(0, 5).join(', ')}`);
    }
    return { areas: requested, scope: within };
  }

  /** The one county a tract selection sits in, or null. */
  function containingCounty(query, dataset, catalog, level) {
    if (level !== 'tract') return null;
    const { areas, scope } = selectedAreas(query, dataset, catalog, level);
    if (scope.scope.kind === 'county') return scope.scope.county;
    const counties = [...new Set(areas.map((g) => g.slice(0, 5)))];
    return counties.length === 1 ? counties[0] : null;
  }

  /**
   * A reference the build computed, looked up for this view. Nothing is
   * computed here; one the build did not write is reported as unavailable.
   */
  function referenceFor(wanted, doc, query, dataset, catalog) {
    const level = param(query, 'level', 'county');
    if (wanted === 'nyc' && doc.nyc && doc.nyc[level]) return doc.nyc[level];
    if (wanted === 'nys' && doc.nys && doc.nys[level]) return doc.nys[level];
    if (wanted === 'containing_county' || wanted === 'containing_borough') {
      const county = containingCounty(query, dataset, catalog, level);
      const found = county && (doc.containing_county || {})[county];
      if (found) return found;
      return {
        benchmark_id: wanted, label: 'The containing county', available: false,
        unavailable_reason: 'this reference applies to tracts inside a single ' +
          'county, and this view does not sit in one',
      };
    }
    return {
      benchmark_id: wanted,
      label: wanted === 'selected' ? 'The selected places combined' : wanted,
      available: false,
      unavailable_reason: (config.unsupported || {}).selected_benchmark
        || 'This reference is not available in the published site.',
    };
  }

  async function get(path, query) {
    if (path === '/api/status') return file('status.json');
    if (path === '/api/dataset') return file('dataset.json');
    if (path === '/api/catalog') return file('catalog.json');

    if (path === '/api/values') {
      const measureId = param(query, 'measure');
      const { meta, values } = await valuesFor(measureId);
      return {
        release_id: meta.release_id, measure_id: meta.measure_id,
        period_label: meta.period_label, values,
      };
    }

    if (path === '/api/geography') {
      return file(`geography/${param(query, 'level', 'county')}.json`);
    }

    if (path === '/api/benchmarks') {
      const level = param(query, 'level', 'county');
      const [doc, dataset, catalog] = await Promise.all([
        file(`benchmarks/${level}.json`), file('dataset.json'), file('catalog.json'),
      ]);
      // A reference that cannot be built here is still listed, so the reason
      // is visible rather than the option silently missing. The containing
      // county is listed only when the view sits in one county, as the
      // service lists it.
      const out = (doc.benchmarks || []).filter((b) => b.benchmark_id !== 'selected');
      const county = containingCounty(query, dataset, catalog, level);
      const containing = county && (doc.containing_county || {})[county];
      if (containing) out.push(containing);
      out.push(...(doc.benchmarks || []).filter((b) => b.benchmark_id === 'selected'));
      return { benchmarks: out };
    }

    if (path === '/api/benchmark') {
      const measureId = param(query, 'measure');
      const wanted = param(query, 'benchmark', 'none');
      const [doc, dataset, catalog] = await Promise.all([
        file(`reference/${measureId}.json`), file('dataset.json'), file('catalog.json'),
      ]);
      return referenceFor(wanted, doc, query, dataset, catalog);
    }

    if (path === '/api/quality') {
      const level = param(query, 'level', 'county');
      const measureId = param(query, 'measure');
      const [dataset, cases, catalog, { values }, reference] = await Promise.all([
        file('dataset.json'), file(`quality/${level}.json`), file('catalog.json'),
        valuesFor(measureId), file(`reference/${measureId}.json`),
      ]);
      const { areas, scope } = selectedAreas(query, dataset, catalog, level);
      const measure = (catalog.levels[level].groups || [])
        .flatMap((g) => g.measures).find((m) => m.measure_id === measureId);
      const benchmarkId = param(query, 'benchmark', 'none');
      const benchmark = benchmarkId === 'none' ? null
        : referenceFor(benchmarkId, reference, query, dataset, catalog);
      const question = (reference.questions || {})[level] || {};
      return {
        quality: {
          uncertainty: staticUncertainty(cases.uncertainty_templates, areas,
                                         values, measure ? measure.unit : ''),
          comparison: cases.comparison[comparisonCase(areas.length, benchmark)],
          freshness: cases.freshness,
        },
        selection: {
          release_id: dataset.release.release_id, level,
          measure_id: measureId, areas, area_count: areas.length,
          requested_areas: query.get('areas') ? areas : [],
          excluded_areas: [], exclusion_reason: '',
          scope: scope.scope.code, scope_label: scope.label,
        },
        question_id: areas.length === 1 ? question.one : question.many,
      };
    }

    if (path === '/api/hospitals') return file('hospitals/registry.json');
    if (path === '/api/hospitals/certification') return file('hospitals/certification.json');

    if (path === '/api/projects') return { projects: [] };

    const err = new Error(
      `${path} needs the local Python service, which a published site does ` +
      'not run.');
    err.kind = 'static_unsupported';
    throw err;
  }

  return {
    get,
    unsupported: config.unsupported || {},
    manifest: () => file('manifest.json'),
    values: valuesFor,
    areaOrder: order,
  };
}

if (typeof module === 'object' && module.exports) {
  module.exports = {
    decodeValues, staticUncertainty, comparisonCase, buildCsv, csvNumber,
    csvField, CSV_COLUMNS, createStaticBackend,
  };
}
