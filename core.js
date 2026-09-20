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

if (typeof module === 'object' && module.exports) {
  module.exports = {
    createLoadGate, classBreaks, classIndex, legendRanges, coverageSentences,
  };
}
