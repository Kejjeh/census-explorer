/* Deterministic tests for web/core.js.
 *
 * Every case here resolves its promises in an order the test chooses, so the
 * out-of-order and failure paths are exercised without timing or a browser.
 *
 * Run by tests/test_ui_core.py through `node --test`, which skips when Node is
 * not installed. Node is not a dependency of this project: it is the runtime
 * the page's own code already runs in, and nothing in the Python package, the
 * service or the offline suite needs it.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const {
  createLoadGate, classBreaks, classIndex, legendRanges, coverageSentences,
} = require('../../web/core.js');

/** A promise whose settlement this test controls. */
function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

// ---------------------------------------------------------------- the gate

test('an older load that finishes last does not overwrite a newer one', async () => {
  const gate = createLoadGate();
  const committed = [];
  const first = deferred();
  const second = deferred();

  const a = gate.run(() => first.promise, { commit: (v) => committed.push(v) });
  const b = gate.run(() => second.promise, { commit: (v) => committed.push(v) });

  second.resolve('new');       // the newer request answers first
  assert.deepStrictEqual((await b).outcome, 'committed');
  first.resolve('old');        // the older one answers afterwards
  assert.deepStrictEqual((await a).outcome, 'stale');

  assert.deepStrictEqual(committed, ['new'],
    'the stale response was allowed to overwrite the current one');
});

test('a stale failure does not blank a view that loaded', async () => {
  const gate = createLoadGate();
  const events = [];
  const first = deferred();
  const second = deferred();

  const a = gate.run(() => first.promise, {
    commit: (v) => events.push(`commit:${v}`),
    fail: (e) => events.push(`fail:${e.message}`),
  });
  const b = gate.run(() => second.promise, {
    commit: (v) => events.push(`commit:${v}`),
    fail: (e) => events.push(`fail:${e.message}`),
  });

  second.resolve('new');
  await b;
  first.reject(new Error('older request failed'));
  assert.strictEqual((await a).outcome, 'stale');

  assert.deepStrictEqual(events, ['commit:new'],
    'a stale error reached the page');
});

test('the current load reports its own failure', async () => {
  const gate = createLoadGate();
  const events = [];
  const result = await gate.run(
    () => Promise.reject(new Error('the service said no')),
    { commit: () => events.push('commit'), fail: (e) => events.push(e.message) });
  assert.strictEqual(result.outcome, 'failed');
  assert.deepStrictEqual(events, ['the service said no']);
});

test('start runs for every attempt and settle only for the current one', async () => {
  const gate = createLoadGate();
  const starts = []; const settles = [];
  const first = deferred(); const second = deferred();
  const handlers = (tag) => ({
    start: () => starts.push(tag),
    settle: () => settles.push(tag),
    commit: () => {},
  });
  const a = gate.run(() => first.promise, handlers('a'));
  const b = gate.run(() => second.promise, handlers('b'));
  second.resolve(1); await b;
  first.resolve(2); await a;
  assert.deepStrictEqual(starts, ['a', 'b'],
    'a load that will turn out stale must still show it started');
  assert.deepStrictEqual(settles, ['b'],
    'a stale load re-enabled the interface behind a newer one');
});

test('invalidate makes an in-flight load stale without starting another', async () => {
  const gate = createLoadGate();
  const committed = [];
  const d = deferred();
  const run = gate.run(() => d.promise, { commit: (v) => committed.push(v) });
  gate.invalidate();
  d.resolve('abandoned');
  assert.strictEqual((await run).outcome, 'stale');
  assert.deepStrictEqual(committed, []);
});

test('three overlapping loads commit only the newest, whatever the order', async () => {
  const gate = createLoadGate();
  const committed = [];
  const d = [deferred(), deferred(), deferred()];
  const runs = d.map((x, i) => gate.run(() => x.promise,
    { commit: () => committed.push(i) }));
  d[1].resolve(); d[0].resolve(); d[2].resolve();
  const outcomes = (await Promise.all(runs)).map((r) => r.outcome);
  assert.deepStrictEqual(outcomes, ['stale', 'stale', 'committed']);
  assert.deepStrictEqual(committed, [2]);
});

// -------------------------------------------------------------- map classes

test('one area yields one class and no break points', () => {
  const b = classBreaks([53.7]);
  assert.deepStrictEqual(b.cuts, []);
  assert.strictEqual(b.classes, 1);
  assert.strictEqual(b.min, 53.7);
  assert.strictEqual(b.max, 53.7);
});

test('areas that all share a value yield one class', () => {
  const b = classBreaks([12, 12, 12, 12]);
  assert.deepStrictEqual(b.cuts, []);
  assert.strictEqual(b.classes, 1);
});

test('no usable value yields no classes at all', () => {
  assert.strictEqual(classBreaks([]).classes, 0);
  assert.strictEqual(classBreaks([null, undefined, NaN, Infinity]).classes, 0);
});

test('tied values never produce a class no area falls in', () => {
  // Heavily tied at both ends: naive quantiles land on the minimum and open
  // empty classes.
  const values = [1, 1, 1, 1, 1, 1, 1, 9, 9, 9, 9, 9, 9, 9];
  const b = classBreaks(values);
  assert.strictEqual(b.classes, 2, 'two distinct values need exactly two classes');
  b.cuts.forEach((c) => assert.ok(c > b.min, `a break sat on the minimum: ${c}`));
  const counts = new Array(b.classes).fill(0);
  values.forEach((v) => { counts[classIndex(v, b.cuts)] += 1; });
  counts.forEach((c, i) => assert.ok(c > 0, `class ${i} holds no area`));
});

test('every break is a value that actually occurs', () => {
  const values = [3, 11, 11, 14, 22, 40, 41, 58];
  const b = classBreaks(values);
  b.cuts.forEach((c) => assert.ok(values.includes(c),
    `the legend shows ${c}, which nobody measured`));
});

test('two distinct values yield two classes, not five empty ones', () => {
  const b = classBreaks([10, 20], 5);
  assert.strictEqual(b.classes, 2);
  assert.deepStrictEqual(b.cuts, [20]);
  assert.strictEqual(classIndex(10, b.cuts), 0);
  assert.strictEqual(classIndex(20, b.cuts), 1);
});

test('a spread of values fills the requested number of classes', () => {
  const b = classBreaks([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 5);
  assert.strictEqual(b.classes, 5);
  assert.deepStrictEqual([...b.cuts].sort((x, y) => x - y), b.cuts);
});

test('class index puts a value in the class the legend shows', () => {
  const values = [0, 10, 20, 30, 40, 50];
  const b = classBreaks(values, 5);
  assert.strictEqual(classIndex(null, b.cuts), null,
    'a missing value must not be shaded as a low one');
  assert.strictEqual(classIndex(b.min, b.cuts), 0);
  assert.strictEqual(classIndex(b.max, b.cuts), b.cuts.length);
  const counts = new Array(b.classes).fill(0);
  values.forEach((v) => { counts[classIndex(v, b.cuts)] += 1; });
  counts.forEach((c, i) => assert.ok(c > 0, `class ${i} holds no area`));
});

test('one class is labelled by its own single range', () => {
  const ranges = legendRanges(classBreaks([53.7]));
  assert.deepStrictEqual(ranges, [{ from: 53.7, to: 53.7 }]);
});

test('legend ranges are closed at both ends and cover every class', () => {
  const b = classBreaks([1, 4, 9, 16, 25, 36, 49], 5);
  const ranges = legendRanges(b);
  assert.strictEqual(ranges.length, b.classes);
  assert.strictEqual(ranges[0].from, b.min);
  assert.strictEqual(ranges[ranges.length - 1].to, b.max);
});

// ----------------------------------------------------------------- coverage

const TRACT = { noun: 'census tract', nounPlural: 'census tracts', vintage: 'GENZ2023' };

test('a view whose areas all drew does not borrow the build-wide shortfall', () => {
  const [first, second] = coverageSentences({
    inViewTotal: 1, inViewDrawn: 1, datasetUnmatched: 3, datasetTotal: 2327,
    unmatchedInView: 0, ...TRACT,
  });
  assert.match(first, /The single census tract in view is drawn\./);
  assert.match(second, /Across the whole build, 3 of 2,327 census tracts/);
  assert.match(second, /none of them is in this view/);
  assert.ok(!/in this export/.test(first + second),
    'areas outside the view were claimed to be in this export');
});

test('a view that could not draw some of its own areas says so', () => {
  const lines = coverageSentences({
    inViewTotal: 2327, inViewDrawn: 2324, datasetUnmatched: 3, datasetTotal: 2327,
    unmatchedInView: 3, ...TRACT,
  });
  assert.match(lines[0], /3 of the 2,327 census tracts in view/);
  assert.match(lines[0], /still in the table and in this export/);
  assert.match(lines[1], /3 of them are in this view/);
});

test('a single undrawn area is described in the singular', () => {
  const lines = coverageSentences({
    inViewTotal: 4, inViewDrawn: 3, datasetUnmatched: 1, datasetTotal: 10,
    unmatchedInView: 1, ...TRACT,
  });
  assert.match(lines[0], /1 of the 4 census tracts in view has no published boundary/);
  assert.match(lines[0], /it is still in the table/);
});

test('a build with every area drawn says nothing build-wide', () => {
  const lines = coverageSentences({
    inViewTotal: 5, inViewDrawn: 5, datasetUnmatched: 0, datasetTotal: 5,
    noun: 'borough', nounPlural: 'boroughs', vintage: 'GENZ2023',
  });
  assert.strictEqual(lines.length, 1);
  assert.match(lines[0], /Every one of the 5 boroughs in view is drawn\./);
});
