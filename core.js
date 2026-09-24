/* Census Explorer — logic with no DOM, no network and no global state.
 *
 * Everything here is a pure function or a small object whose behaviour can be
 * driven from a test: which of several overlapping loads is allowed to commit,
 * how a set of values divides into shaded classes, and what the map may
 * truthfully claim about the areas it did and did not draw.
 *
 * The page loads this file before app.js; the tests load it directly. It must
 * therefore not touch `window`, `document` or `fetch`.
 */
'use strict';

/* --------------------------------------------------------------- loading */

/**
 * Serialises overlapping loads so that only the newest one is allowed to
 * change anything.
 *
 * Switching measure, level, place scope or reference starts a load. Those
 * controls are faster than the service, so several loads can be in flight at
 * once and they can finish in any order. Without this, an older response
 * overwrites a newer one and the page then labels the old numbers with the new
 * measure's name. A stale failure is just as damaging: it would blank a view
 * that actually loaded.
 */
function createLoadGate() {
  let current = 0;
  return {
    /** The generation a caller must still match to be allowed to commit. */
    get generation() { return current; },

    /** Make every load now in flight stale without starting a new one. */
    invalidate() { current += 1; },

    /**
     * Run `work` and hand its outcome to `handlers` only if no newer run has
     * started in the meantime.
     *
     * `start` runs for every attempt, because a load that will turn out to be
     * stale still has to show that something is happening. `commit`, `fail`
     * and `settle` run only for the newest attempt.
     */
    async run(work, handlers) {
      current += 1;
      const token = current;
      const h = handlers || {};
      if (h.start) h.start(token);
      let value;
      let error = null;
      try {
        value = await work(token);
      } catch (e) {
        error = e || new Error('the load failed without a reason');
      }
      if (token !== current) return { token, outcome: 'stale' };
      if (error) {
        if (h.fail) h.fail(error, token);
        if (h.settle) h.settle(token);
        return { token, outcome: 'failed', error };
      }
      if (h.commit) h.commit(value, token);
      if (h.settle) h.settle(token);
      return { token, outcome: 'committed', value };
    },
  };
}

/* ----------------------------------------------------------- map classes */

/**
 * Break points for a shaded map, and the range they divide.
 *
 * Every break is a value that actually occurs in the data, so the legend never
 * shows a number nobody measured, and a break is kept only when the class it
 * closes contains something. A break equal to the smallest value would open an
 * empty class beneath it; a break that no value falls short of would draw a
 * shade nothing uses. One area, or many areas that share a value, therefore
 * yields no breaks and exactly one class.
 */
function classBreaks(values, maxClasses = 5) {
  const usable = (values || []).filter((v) => typeof v === 'number' && isFinite(v));
  if (!usable.length) return { cuts: [], min: null, max: null, classes: 0 };
  const sorted = [...usable].sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  if (min === max) return { cuts: [], min, max, classes: 1 };

  const n = sorted.length;
  const cuts = [];
  let lower = min;
  for (let i = 1; i < maxClasses; i += 1) {
    const candidate = sorted[Math.min(n - 1, Math.floor((i * n) / maxClasses))];
    if (candidate <= min) continue;                       // empty first class
    if (cuts.length && candidate <= cuts[cuts.length - 1]) continue;  // repeat
    // The class this break closes has to hold at least one area, or the
    // legend shows a shade the map never draws.
    if (!sorted.some((v) => v >= lower && v < candidate)) continue;
    cuts.push(candidate);
    lower = candidate;
  }
  return { cuts, min, max, classes: cuts.length + 1 };
}

/** Which shading class a value falls in, or null when it has no value. */
function classIndex(value, cuts) {
  if (value === null || value === undefined || !isFinite(value)) return null;
  for (let i = 0; i < cuts.length; i += 1) if (value < cuts[i]) return i;
  return cuts.length;
}

/**
 * The closed range each class covers, so the legend can show its ends rather
 * than only its interior breaks.
 */
function legendRanges(breaks) {
  const { cuts, min, max, classes } = breaks;
  if (!classes) return [];
  const edges = [min, ...cuts, max];
  const out = [];
  for (let i = 0; i < classes; i += 1) out.push({ from: edges[i], to: edges[i + 1] });
  return out;
}

/* -------------------------------------------------------------- coverage */

/**
 * What the map may truthfully say about coverage.
 *
 * Two different facts get confused easily: how many of the areas *in this
 * view* could not be drawn, and how many areas *in the whole build* have no
 * boundary at this vintage. A view narrowed to one borough that drew fine must
 * not inherit the second number, and must not claim that areas outside it are
 * "in every export" when the export covers only what is in view.
 */
function coverageSentences(input) {
  const {
    inViewTotal = 0, inViewDrawn = 0,
    datasetUnmatched = 0, datasetTotal = 0,
    unmatchedInView = 0, noun = 'area', nounPlural = 'areas', vintage = '',
  } = input || {};
  const missing = Math.max(0, inViewTotal - inViewDrawn);
  const out = [];
  if (missing > 0) {
    out.push(`${missing} of the ${inViewTotal.toLocaleString('en-US')} ` +
      `${inViewTotal === 1 ? noun : nounPlural} in view ` +
      `${missing === 1 ? 'has' : 'have'} no published boundary at this geography ` +
      `vintage${vintage ? ` (${vintage})` : ''} and ` +
      `${missing === 1 ? 'is' : 'are'} not drawn; ` +
      `${missing === 1 ? 'it is' : 'they are'} still in the table and in the ` +
      'data for this selection.');
  } else if (inViewTotal === 1) {
    out.push(`The single ${noun} in view is drawn.`);
  } else if (inViewTotal > 0) {
    out.push(`Every one of the ${inViewTotal.toLocaleString('en-US')} ` +
      `${nounPlural} in view is drawn.`);
  }
  if (datasetUnmatched > 0) {
    const where = unmatchedInView > 0
      ? `${unmatchedInView} of them ${unmatchedInView === 1 ? 'is' : 'are'} in this view`
      : 'none of them is in this view';
    out.push(`Across the whole build, ${datasetUnmatched} of ` +
      `${datasetTotal.toLocaleString('en-US')} ${nounPlural} have no boundary at ` +
      `this vintage; ${where}.`);
  }
  return out;
}

/* ----------------------------------------------------------------- scope */

/**
 * Which places a view covers: New York City, New York State, or the census
 * tracts of one county. This mirrors `census_explorer/scope.py` line for line,
 * and a test runs both over the real build and compares every answer, so the
 * published site and the local service cannot disagree about what "Erie
 * County's tracts" means.
 *
 * A view that names no scope is New York City. That is what every link and
 * saved view meant before the build covered the state, and one made then
 * must not quietly widen to the whole state now.
 */
const SCOPE_DEFAULT = 'nyc';

function parseScope(text) {
  if (text === null || text === undefined || text === '') {
    return { kind: SCOPE_DEFAULT, county: null, code: SCOPE_DEFAULT };
  }
  if (text === 'nyc' || text === 'nys') return { kind: text, county: null, code: text };
  const m = /^county:(\d{5})$/.exec(String(text));
  if (m) return { kind: 'county', county: m[1], code: `county:${m[1]}` };
  throw new Error(`unknown scope '${text}'; use 'nyc', 'nys' or 'county:<GEOID>'`);
}

function resolveScope(scope, level, areas, boroughs, statewide) {
  const atLevel = areas.filter((a) => a.level === level).map((a) => a.geoid).sort();
  const counties = new Set(areas.filter((a) => a.level === 'county').map((a) => a.geoid));
  const city = new Set(boroughs);
  if (scope.kind === 'nyc') {
    const missing = [...city].filter((g) => !counties.has(g)).sort();
    if (missing.length && statewide) {
      throw new Error('New York City cannot be shown: its documented counties ' +
        `${missing.join(', ')} are not in this build`);
    }
    if (level === 'county') return atLevel.filter((g) => city.has(g));
    return atLevel.filter((g) => city.has(g.slice(0, 5)));
  }
  if (scope.kind === 'nys') {
    if (!statewide) throw new Error('this build does not cover the whole state');
    return atLevel;
  }
  if (scope.kind === 'county') {
    if (level !== 'tract') {
      throw new Error("a single-county scope lists that county's census tracts; " +
        'switch to the tract level to use it');
    }
    if (!counties.has(scope.county)) throw new Error(`county ${scope.county} is not in this build`);
    return atLevel.filter((g) => g.slice(0, 5) === scope.county);
  }
  throw new Error(`unknown scope kind '${scope.kind}'`);
}

/** The scope in words with its size: `scope.describe` in the service. */
function describeScope(scope, level, count, countyName, partial) {
  const n = count.toLocaleString('en-US');
  if (scope.kind === 'nyc' && partial) {
    const noun = level === 'county' ? 'counties' : 'census tracts';
    return `${n} ${noun} in the New York City counties this build has (not all five)`;
  }
  if (level === 'county') {
    if (scope.kind === 'nyc') return `the ${n} New York City boroughs`;
    return `all ${n} counties in New York State`;
  }
  if (scope.kind === 'nyc') return `${n} census tracts in New York City`;
  if (scope.kind === 'nys') return `${n} census tracts in New York State`;
  return `${n} census tracts in ${countyName || scope.county}`;
}

/**
 * The inspected place and the comparison that survive a change of geography
 * level. A GEOID names a place at one level only, so a county kept as the
 * inspected place in a tract view would be offered for a tract comparison
 * and written into a share link that the link's own checks then refuse.
 * Anything not at the new level is dropped. A place that is at the new level
 * but outside the scope is kept: inspecting it is allowed, and the page
 * labels it as outside the view.
 */
function selectionForLevel(selection, level, areas) {
  const at = new Set(areas.filter((a) => a.level === level).map((a) => a.geoid));
  const pick = selection && selection.pick && at.has(selection.pick) ? selection.pick : null;
  const compare = ((selection && selection.compare) || []).filter((g) => at.has(g));
  return { pick, compare };
}

/* ------------------------------------------------------------ pagination */

/**
 * One page of a long list. Every row stays reachable: a page past the end
 * is pulled back to the last one rather than showing nothing.
 */
function pageWindow(total, pageSize, page) {
  const size = Math.max(1, Math.floor(pageSize) || 1);
  const pages = Math.max(1, Math.ceil(total / size));
  const p = Math.min(pages - 1, Math.max(0, Math.floor(page) || 0));
  const start = total ? p * size : 0;
  const end = Math.min(total, start + size);
  return { page: p, pages, start, end, size, total };
}

/** The page a row at `index` sits on; -1 (not listed) stays on the first. */
function pageOfIndex(index, pageSize) {
  return index < 0 ? 0 : Math.floor(index / Math.max(1, pageSize));
}

/* ---------------------------------------------------- comparison chart */

/** Axis steps of 1, 2 or 5 times a power of ten. */
function niceStep(span, target) {
  const raw = span / Math.max(1, target || 5);
  if (!(raw > 0) || !isFinite(raw)) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / mag;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag;
}

/**
 * What a chart of two places, with their published 90% margins of error,
 * may draw.
 *
 * Nothing is computed that the table does not already show. Each interval
 * is the published estimate plus and minus its published margin of error.
 * The axis always includes zero and always contains every interval in full:
 * an interval is never cut short at the edge. When an interval reaches past
 * what the quantity can take (below zero, or above 100 percent) the axis
 * extends to show it as published and a note says so. A place with no
 * estimate, or an estimate without a margin of error, is drawn as exactly
 * that; a missing margin is never drawn as a zero-width one.
 */
function intervalChartModel(input) {
  const unit = (input && input.unit) || 'persons';
  const rows = ((input && input.rows) || []).map((r) => {
    const v = r.value || {};
    const out = { key: r.key, label: r.label, e: null, lo: null, hi: null, m: null };
    if (v.es !== 'ok' || typeof v.e !== 'number' || !isFinite(v.e)) {
      out.state = 'no_estimate';
      out.reason = r.reason || 'no published estimate';
      return out;
    }
    out.e = v.e;
    if (v.ctl === true) { out.state = 'controlled'; return out; }
    if (v.ms === 'ok' && typeof v.m === 'number' && isFinite(v.m)) {
      out.state = 'interval';
      out.m = v.m;
      out.lo = v.e - v.m;
      out.hi = v.e + v.m;
      return out;
    }
    out.state = 'no_moe';
    out.reason = r.reason || 'margin of error unavailable';
    return out;
  });
  const points = [0];
  rows.forEach((r) => {
    if (r.e !== null) points.push(r.e);
    if (r.lo !== null) points.push(r.lo, r.hi);
  });
  let lo = Math.min(...points);
  let hi = Math.max(...points);
  if (lo === hi) hi = unit === 'percent' ? 100 : 1;
  const step = niceStep(hi - lo, 5);
  let dlo = Math.floor(lo / step) * step;
  let dhi = Math.ceil(hi / step) * step;
  // A share's axis stops at 100 unless something published goes past it.
  if (unit === 'percent' && hi <= 100 && dhi > 100) dhi = 100;
  if (dlo === dhi) dhi = dlo + step;
  const ticks = [];
  for (let t = dlo; t <= dhi + step / 1e6; t += step) ticks.push(Math.round(t / step) * step);
  if (ticks[ticks.length - 1] < dhi) ticks.push(dhi);
  const notes = [];
  rows.forEach((r) => {
    if (r.state !== 'interval') return;
    if (r.lo < 0) {
      notes.push(`The published margin of error for ${r.label} reaches below zero, ` +
        'which this quantity cannot. The interval is drawn as published and the ' +
        'axis extends below zero to show all of it.');
    }
    if (unit === 'percent' && r.hi > 100) {
      notes.push(`The published margin of error for ${r.label} reaches above 100%, ` +
        'which a share cannot. The interval is drawn as published and the axis ' +
        'extends past 100% to show all of it.');
    }
  });
  return { unit, domain: [dlo, dhi], ticks, rows, notes };
}

function svgEscape(s) {
  return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * The chart as an SVG string: one row per place on one shared axis.
 *
 * `opts.format(value)` formats an estimate, `opts.formatMoe(m)` a margin, so
 * the chart prints exactly what the table prints. The title and description
 * repeat every value in words for a screen reader; the text comparison and
 * the table stay on the page beside it.
 */
function intervalChartSvg(model, opts) {
  const o = opts || {};
  const fmtV = o.format || ((v) => String(v));
  const fmtM = o.formatMoe || ((m) => `±${m}`);
  const id = o.id || 'cmp-chart';
  // Side margins leave room for the end tick labels, which are centred on
  // their ticks and would otherwise be cut off (a lost minus sign reads as a
  // different number).
  const W = 360; const left = 28; const right = 28; const rowH = 52; const top = 8;
  const axisY = top + model.rows.length * rowH + 6;
  const H = axisY + 34;
  const [d0, d1] = model.domain;
  const x = (v) => left + ((v - d0) / (d1 - d0)) * (W - left - right);
  const words = model.rows.map((r) => {
    if (r.state === 'no_estimate') return `${r.label}: no estimate (${r.reason}).`;
    if (r.state === 'controlled') {
      return `${r.label}: ${fmtV(r.e)}, a controlled total with no sampling error.`;
    }
    if (r.state === 'no_moe') return `${r.label}: ${fmtV(r.e)}; ${r.reason}.`;
    return `${r.label}: ${fmtV(r.e)}, ${fmtM(r.m)} at 90% confidence ` +
      `(${fmtV(r.lo)} to ${fmtV(r.hi)}).`;
  });
  const parts = [];
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" ` +
    `class="interval-chart" role="img" aria-labelledby="${id}-t ${id}-d">`);
  parts.push(`<title id="${id}-t">${svgEscape(o.title || 'Comparison')}</title>`);
  parts.push(`<desc id="${id}-d">${svgEscape([o.desc || '', ...words, ...model.notes]
    .filter(Boolean).join(' '))}</desc>`);
  // Grid and the zero line, which is always inside the axis.
  model.ticks.forEach((t) => {
    parts.push(`<line class="ic-grid${t === 0 ? ' ic-zero' : ''}" x1="${x(t).toFixed(1)}" ` +
      `x2="${x(t).toFixed(1)}" y1="${top}" y2="${axisY}"/>`);
    parts.push(`<text class="ic-tick" x="${x(t).toFixed(1)}" y="${axisY + 14}" ` +
      `text-anchor="middle">${svgEscape(fmtV(t))}</text>`);
  });
  parts.push(`<line class="ic-axis" x1="${left}" x2="${W - right}" y1="${axisY}" y2="${axisY}"/>`);
  if (o.axisLabel) {
    parts.push(`<text class="ic-axis-label" x="${W / 2}" y="${axisY + 30}" ` +
      `text-anchor="middle">${svgEscape(o.axisLabel)}</text>`);
  }
  model.rows.forEach((r, i) => {
    const y0 = top + i * rowH;
    const cy = y0 + 34;
    let caption;
    if (r.state === 'no_estimate') caption = `${r.label} — no estimate`;
    else if (r.state === 'controlled') caption = `${r.label} — ${fmtV(r.e)}, controlled total`;
    else if (r.state === 'no_moe') caption = `${r.label} — ${fmtV(r.e)}, margin of error unavailable`;
    else caption = `${r.label} — ${fmtV(r.e)} (${fmtM(r.m)})`;
    parts.push(`<g class="ic-row" data-state="${r.state}" data-key="${svgEscape(r.key)}">`);
    parts.push(`<text class="ic-label" x="4" y="${y0 + 16}">${svgEscape(caption)}</text>`);
    if (r.state === 'interval') {
      parts.push(`<line class="ic-interval" x1="${x(r.lo).toFixed(1)}" x2="${x(r.hi).toFixed(1)}" ` +
        `y1="${cy}" y2="${cy}"/>`);
      [r.lo, r.hi].forEach((v) => parts.push(`<line class="ic-cap" x1="${x(v).toFixed(1)}" ` +
        `x2="${x(v).toFixed(1)}" y1="${cy - 6}" y2="${cy + 6}"/>`));
      parts.push(`<circle class="ic-point" cx="${x(r.e).toFixed(1)}" cy="${cy}" r="5"/>`);
    } else if (r.state === 'controlled') {
      const cx = x(r.e);
      parts.push(`<path class="ic-point ic-controlled" d="M${cx.toFixed(1)} ${cy - 6}` +
        `L${(cx + 6).toFixed(1)} ${cy}L${cx.toFixed(1)} ${cy + 6}L${(cx - 6).toFixed(1)} ${cy}Z"/>`);
    } else if (r.state === 'no_moe') {
      parts.push(`<circle class="ic-point ic-hollow" cx="${x(r.e).toFixed(1)}" cy="${cy}" r="5"/>`);
    } else {
      parts.push(`<text class="ic-missing" x="4" y="${cy + 4}">not drawn: ` +
        `${svgEscape(r.reason)}</text>`);
    }
    parts.push('</g>');
  });
  parts.push('</svg>');
  return parts.join('');
}

if (typeof module === 'object' && module.exports) {
  module.exports = {
    createLoadGate, classBreaks, classIndex, legendRanges, coverageSentences,
    SCOPE_DEFAULT, parseScope, resolveScope, describeScope, selectionForLevel,
    pageWindow, pageOfIndex, niceStep, intervalChartModel, intervalChartSvg,
  };
}
