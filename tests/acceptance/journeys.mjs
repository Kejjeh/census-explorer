// Live browser acceptance: the statewide explorer, local service and static site.
//
//   node tests/acceptance/journeys.mjs \
//     --local  http://127.0.0.1:8765/ \
//     --static http://127.0.0.1:8899/census-explorer/ \
//     [--out artifacts/acceptance/journeys-<stamp>]
//
// Either target may be omitted. Runs against a LIVE build only: the journeys
// name real 2019-2023 ACS geographies (Erie County 36029, Suffolk County
// 36103, Bronx tract 36005000100), and a fixture build is refused rather than
// half-tested. Fixture behaviour is covered by the offline suite in tests/.
//
// Writes report.md, report.json and screenshots to the output directory and
// exits non-zero if any check fails. See docs/ACCEPTANCE.md.

import fs from 'node:fs';
import path from 'node:path';
import { loadPlaywright, parseArgs, outDir, createReport, requireLiveTargets } from './lib.mjs';

const args = parseArgs(process.argv.slice(2), { local: '', static: '', out: '', only: '', 'preflight-only': false });
if (!args.local && !args.static) {
  console.error('give --local and/or --static'); process.exit(2);
}
// Every target's own status metadata is checked first, before Playwright is
// loaded or anything is rendered or reported. A fixture build, or one whose
// mode is missing or unknown, is refused.
await requireLiveTargets([['local', args.local], ['static', args.static]].filter(([, u]) => u));
if (args['preflight-only']) process.exit(0);
const { chromium } = await loadPlaywright();
const dir = outDir(args.out, 'journeys');
const report = createReport(dir, 'Census Explorer browser acceptance (live data)');
const DESKTOP = { width: 1480, height: 1000 };
const NARROW = { width: 390, height: 844 };

const browser = await chromium.launch();

/* ------------------------------------------------------------ helpers */

async function openApp(mode, url, viewport) {
  const context = await browser.newContext({ viewport, acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  const outside = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  page.on('request', (r) => {
    if (!r.url().startsWith(new URL(url).origin) && !r.url().startsWith('blob:')
        && !r.url().startsWith('data:')) outside.push(r.url());
  });
  await page.goto(url);
  await page.waitForSelector('#map svg path', { timeout: 60000 });
  await idle(page);
  const mode_ = await page.evaluate(() => state.status && state.status.data_mode);
  if (mode_ !== 'live') {
    console.error(`${url} serves data_mode=${mode_}. This acceptance run needs the live ` +
      'build; fixture behaviour is tested offline in tests/.');
    process.exit(2);
  }
  return { context, page, errors, outside, mode, url };
}

/** Wait until one complete load has landed, optionally for a given scope. */
async function idle(page, scope) {
  await page.waitForTimeout(60);
  await page.waitForFunction((w) => state.ready && !state.loading && state.selectionJson
    && (!w || (state.scope === w && state.selectionJson.scope === w)), scope || null,
  { timeout: 60000 });
  await page.waitForTimeout(120);
}

const text = async (page, sel) => ((await page.textContent(sel)) || '').replace(/\s+/g, ' ').trim();

async function shot(page, name) {
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage: false });
}

/**
 * What a 390-pixel reader can and cannot reach: horizontal overflow, and
 * any visible control that extends past the viewport or cuts off its own
 * label. Measured, not assumed.
 */
async function layoutAudit(page, label) {
  const r = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const bad = [];
    const small = [];
    document.querySelectorAll('button, a[href], input, select, summary, [tabindex="0"]').forEach((el) => {
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none') return;
      const rect = el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const name = (el.getAttribute('aria-label') || el.textContent || el.id || el.tagName)
        .replace(/\s+/g, ' ').trim().slice(0, 50);
      if (rect.right > vw + 0.5 || rect.left < -0.5) bad.push(`${name}: x ${Math.round(rect.left)}–${Math.round(rect.right)} of ${vw}`);
      else if (el.tagName === 'BUTTON' && el.scrollWidth > el.clientWidth + 1) bad.push(`${name}: label cut (${el.scrollWidth} > ${el.clientWidth})`);
      if (el.tagName !== 'A' && (rect.height < 24 || rect.width < 24) && !el.closest('#table-body')) small.push(`${name} ${Math.round(rect.width)}×${Math.round(rect.height)}`);
    });
    return {
      innerWidth: window.innerWidth, clientWidth: vw, scrollWidth: document.documentElement.scrollWidth,
      dpr: window.devicePixelRatio, bad, small,
    };
  });
  report.check(r.innerWidth === 390 && r.clientWidth <= 390, `${label}: measured viewport`,
    `innerWidth ${r.innerWidth}, clientWidth ${r.clientWidth}, devicePixelRatio ${r.dpr}`);
  report.check(r.scrollWidth <= r.clientWidth, `${label}: no horizontal page overflow`, `scrollWidth ${r.scrollWidth}`);
  report.check(r.bad.length === 0, `${label}: no control extends past the viewport or cuts off its label`, r.bad.length ? r.bad : undefined);
  if (r.small.length) report.note(`${label}: controls under 24 CSS px in one dimension`, r.small.slice(0, 12));
  return r;
}

/** Press Tab (or Shift+Tab) until the focused element matches, and say how many. */
async function tabTo(page, predicateSource, what, { back = false, max = 400 } = {}) {
  for (let i = 1; i <= max; i += 1) {
    await page.keyboard.press(back ? 'Shift+Tab' : 'Tab');
    // eslint-disable-next-line no-new-func
    const hit = await page.evaluate(new Function(`const el = document.activeElement; return (${predicateSource});`));
    if (hit) return i;
  }
  report.check(false, `keyboard reaches ${what}`, `not reached in ${max} presses`);
  return null;
}

async function focusVisible(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return 'nothing focused';
    const cs = getComputedStyle(el);
    const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
    const ring = cs.boxShadow && cs.boxShadow !== 'none';
    const rect = el.getBoundingClientRect();
    const onScreen = rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
    return (outline || ring) && onScreen ? true : `outline ${cs.outlineStyle} ${cs.outlineWidth}; ring ${cs.boxShadow}; on screen ${onScreen}`;
  });
}

async function finish(app, label) {
  const expected = app.errors.filter((e) => !/ERR_FAILED|Failed to load resource/.test(e) || !app.expectFailure);
  report.check(expected.length === 0, `${label}: no unexpected console or page errors`, expected.length ? expected : undefined);
  if (app.mode === 'static') {
    report.check(app.outside.length === 0, `${label}: no request outside the site's origin`, app.outside.length ? app.outside.slice(0, 5) : undefined);
  }
  await app.context.close();
}

/* ------------------------------------------------------------ journeys */

async function countyToTract(mode, url) {
  report.section(`${mode}: county to tract, then share and compare (desktop ${DESKTOP.width}×${DESKTOP.height})`);
  const app = await openApp(mode, url, DESKTOP);
  const { page } = app;
  const starters = await page.$$eval('.starter .s-meta', (ns) => ns.map((n) => n.textContent).filter((x) => x.startsWith('Opens')));
  report.check(starters.length > 0 && starters.every((x) => /New York City/.test(x)), 'starter cards name the New York City scope', starters);
  report.check((await text(page, '#scope-bar')).startsWith('Showing the 5 New York City boroughs'), 'default view is the five boroughs');
  await page.click('#region-switch button[data-region="nys"]'); await idle(page, 'nys');
  report.check((await text(page, '#scope-bar')).startsWith('Showing all 62 counties in New York State'), 'New York State shows 62 counties');
  await page.fill('#place-search', 'Buffalo'); await page.waitForTimeout(150);
  report.check(/Cities, towns and neighbourhoods are not geographies here/.test(await text(page, '#place-results')), 'a city search explains that cities are not geographies');
  await page.fill('#place-search', 'Erie'); await page.waitForTimeout(150);
  await page.click('#place-results button:has-text("Erie County")'); await page.waitForTimeout(150);
  await page.click('#place-card button:has-text("Add to comparison")'); await page.waitForTimeout(100);
  await page.click('#place-card button:has-text("Explore this county")');
  const now = await page.evaluate(() => ({ level: state.level, pick: state.pick, compare: state.compare, ready: state.ready,
    brief: document.getElementById('btn-brief').disabled, share: document.getElementById('btn-share').disabled }));
  report.check(now.level === 'tract' && now.pick === null && now.compare.length === 0,
    'the county inspected and compared is cleared the moment the tract view is chosen', now);
  report.check(!now.ready && now.brief && (mode !== 'static' || now.share), 'brief and share wait for the tract view to load');
  await idle(page, 'county:36029');
  report.check((await text(page, '#scope-bar')).startsWith('Showing 261 census tracts in Erie County'), 'Erie County shows its 261 census tracts');
  const drawn = await page.$$eval('#map path', (p) => p.length);
  report.check(drawn === 260, 'map draws 260 of them (one water tract has no boundary)', `${drawn} shapes`);
  report.check(/Choose an area/.test(await text(page, '#place-card')), 'the inspector is empty, not offering a county for comparison');
  if (mode === 'static') {
    await page.click('#btn-share'); await page.waitForTimeout(150);
    report.check((await page.inputValue('#share-url')).includes('#view='), 'a share link is produced');
  }
  await shot(page, `${mode}-desktop-erie-tracts`);
  await finish(app, `${mode} county to tract`);
}

async function outsideComparison(mode, url) {
  report.section(`${mode}: comparison kept across a scope change (desktop)`);
  const app = await openApp(mode, url, DESKTOP);
  const { page } = app;
  await page.click('#region-switch button[data-region="nys"]'); await idle(page, 'nys');
  await page.click('.level-switch button[data-level="tract"]'); await idle(page, 'nys');
  await page.selectOption('#county-select', '36029'); await idle(page, 'county:36029');
  for (const g of ['36029990000', '36029940100']) {
    await page.fill('#place-search', g); await page.waitForTimeout(150);
    await page.click('#place-results button'); await page.waitForTimeout(100);
    await page.click('#place-card button:has-text("Add to comparison")'); await page.waitForTimeout(100);
  }
  const first = page.locator('#compare-panel .pc-actions button').first();
  report.check((await first.textContent()) === 'Show only these two', 'inside Erie: "Show only these two"');
  const rows = await page.$$eval('.interval-chart .ic-row', (r) => r.map((x) => x.dataset.state));
  report.check(rows.length === 2 && rows.every((s) => s === 'interval'), 'the chart draws both published intervals', rows);
  report.check(/includes zero/.test(await page.$eval('.interval-chart desc', (d) => d.textContent)), 'chart description says the axis includes zero');
  await page.selectOption('#county-select', '36103'); await idle(page, 'county:36103');
  report.check(/outside the current view/.test(await text(page, '#compare-panel')), 'in Suffolk, both are kept and labelled outside the view');
  const label = await first.textContent();
  report.check(label === "Switch to Erie County's census tracts and show only these two", 'the narrowing action names the scope it switches to', label);
  await first.click(); await idle(page, 'county:36029');
  const r = await page.evaluate(() => ({ blocked: !document.getElementById('blocked').hidden, areas: state.selectionJson.areas }));
  report.check(!r.blocked && r.areas.length === 2, 'no refused load; the view holds exactly the two', r);
  report.check(/Showing 2 of 261 census tracts in Erie County/.test(await text(page, '#scope-bar')), 'the scope line says 2 of Erie County\'s 261');
  await finish(app, `${mode} comparison`);
}

async function shareAndReload(url) {
  report.section('static: share links, reload, older and stale links (desktop)');
  const app = await openApp('static', url, DESKTOP);
  const { page } = app;
  const snapshot = await page.evaluate(() => window.CENSUS_EXPLORER_STATIC.snapshot);
  const measure = await page.evaluate(() => state.measureId);
  await page.click('#region-switch button[data-region="nys"]'); await idle(page, 'nys');
  await page.click('.level-switch button[data-level="tract"]'); await idle(page, 'nys');
  await page.selectOption('#county-select', '36103'); await idle(page, 'county:36103');
  await page.selectOption('#benchmark-select', 'containing_county'); await idle(page, 'county:36103');
  await page.fill('#place-search', '36005000100'); await page.waitForTimeout(150);
  await page.click('#place-results button'); await page.waitForTimeout(150);
  await page.click('#btn-share'); await page.waitForTimeout(150);
  const link = await page.inputValue('#share-url');
  await page.goto('about:blank'); await page.goto(link); await page.waitForSelector('#map svg path'); await idle(page, 'county:36103');
  const back = await page.evaluate(() => ({ scope: state.scope, pick: state.pick, ref: state.benchmark && state.benchmark.label, note: document.getElementById('link-note').hidden }));
  report.check(back.pick === '36005000100' && back.ref === 'Suffolk County' && back.note, 'reloading the link restores scope, reference and an outside inspected place', back);
  await page.reload(); await page.waitForSelector('#map svg path'); await idle(page, 'county:36103');
  report.check(await page.evaluate(() => state.scope === 'county:36103'), 'a browser reload keeps the shared view');
  const v1 = { v: 1, snapshot, release: 'acs5_2023', level: 'tract', measure, areas: [], benchmark: 'none', pick: null, compare: [] };
  const open = async (view) => { await page.goto('about:blank'); await page.goto(url + '#view=' + encodeURIComponent(JSON.stringify(view))); await page.waitForSelector('#map svg path'); await idle(page); };
  await open(v1);
  report.check((await text(page, '#scope-bar')).startsWith('Showing 2,327 census tracts in New York City'), 'a version 1 link opens New York City, not the state');
  await open({ ...v1, areas: ['36029016600'] });
  report.check(/out-of-scope places/.test(await text(page, '#link-note')), 'a version 1 link naming an Erie tract is refused with a notice');
  await open({ ...v1, snapshot: 'an-older-snapshot' });
  report.check(/different published snapshot/.test(await text(page, '#link-note')), 'a link from another snapshot is refused with the existing notice');
  await finish(app, 'static share');
}

async function loadFailure(mode, url) {
  report.section(`${mode}: a genuine load failure, then recovery (desktop)`);
  const app = await openApp(mode, url, DESKTOP);
  app.expectFailure = true;
  const { page } = app;
  let failNext = true;
  await page.route(/foreign_born_share/, async (route) => {
    if (failNext && /values/.test(route.request().url())) { failNext = false; return route.abort(); }
    return route.continue();
  });
  await page.fill('#measure-search', 'Foreign-born share'); await page.waitForTimeout(150);
  await page.locator('.measure', { hasText: 'Foreign-born share' }).first().click();
  await page.waitForSelector('#blocked:not([hidden])', { timeout: 20000 });
  const r = await page.evaluate(() => ({ ready: state.ready, rows: document.querySelectorAll('#table-body tr').length,
    brief: document.getElementById('btn-brief').disabled, exp: document.getElementById('btn-export').disabled }));
  report.check(!r.ready && r.rows === 0 && r.brief && r.exp, 'the failed view is cleared and brief/export are disabled', r);
  await page.focus('#blocked button');
  await page.keyboard.press('Enter'); await idle(page);
  report.check(await page.evaluate(() => document.activeElement.id) === 'scope-bar', 'after Try again, focus lands on the scope line');
  report.check((await text(page, '#measure-title')) === 'Foreign-born share of residents' && await page.evaluate(() => state.ready), 'Try again recovers the requested measure');
  await finish(app, `${mode} load failure`);
}

async function narrowLayout(mode, url) {
  report.section(`${mode}: 390 CSS px layout`);
  const app = await openApp(mode, url, NARROW);
  const { page } = app;
  await layoutAudit(page, 'first view');
  await shot(page, `${mode}-390-first`);
  await page.click('#region-switch button[data-region="nys"]'); await idle(page, 'nys');
  await layoutAudit(page, 'New York State counties');
  await page.click('.level-switch button[data-level="tract"]'); await idle(page, 'nys');
  await page.selectOption('#county-select', '36029'); await idle(page, 'county:36029');
  for (const g of ['36029016600', '36029990000']) {
    await page.fill('#place-search', g); await page.waitForTimeout(150);
    await page.click('#place-results button'); await page.waitForTimeout(100);
    await page.click('#place-card button:has-text("Add to comparison")'); await page.waitForTimeout(100);
  }
  await layoutAudit(page, 'Erie tracts with a two-place comparison');
  await page.screenshot({ path: path.join(dir, `${mode}-390-erie-full.png`), fullPage: true });
  await page.$eval('#compare-panel', (el) => el.scrollIntoView());
  await shot(page, `${mode}-390-compare`);
  await page.$eval('#table-pager', (el) => el.scrollIntoView({ block: 'center' }));
  await shot(page, `${mode}-390-pager`);
  await finish(app, `${mode} 390 layout`);
}

/**
 * Keyboard only, at 390 CSS px: area, level, county to tract, measure,
 * table, comparison, share, brief and export. Only Tab, Shift+Tab, Enter,
 * Space, arrows and typing are used; the mouse is never touched.
 */
async function keyboardOnly(mode, url) {
  report.section(`${mode}: keyboard only at 390 CSS px`);
  const app = await openApp(mode, url, NARROW);
  const { page } = app;
  const vw = await page.evaluate(() => window.innerWidth);
  report.check(vw === 390, 'viewport is 390 CSS px', `${vw}`);
  const step = async (predicate, what, key = 'Enter', opts = {}) => {
    const n = await tabTo(page, predicate, what, opts);
    if (n === null) return false;
    const fv = await focusVisible(page);
    report.check(fv === true, `${what}: reached in ${n} Tab presses${opts.back ? ' (backwards)' : ''}, focus visible`, fv === true ? undefined : fv);
    if (key) await page.keyboard.press(key);
    return true;
  };
  await step(`el.dataset && el.dataset.region === 'nys'`, 'New York State');
  await idle(page, 'nys');
  await step(`el.id === 'place-search'`, 'place search', null);
  await page.keyboard.type('Erie County'); await page.waitForTimeout(150);
  await page.keyboard.press('ArrowDown');
  report.check(await page.evaluate(() => document.activeElement.closest('#place-results') !== null), 'ArrowDown moves into the search results');
  await page.keyboard.press('Enter'); await page.waitForTimeout(150);
  const afterPick = await page.evaluate(() => ({ pick: state.pick, focus: document.activeElement.id || document.activeElement.className || document.activeElement.tagName }));
  report.check(afterPick.pick === '36029', 'Enter on a result inspects Erie County', afterPick);
  report.check(afterPick.focus !== 'BODY', 'focus is not dropped to the page after choosing a result', afterPick.focus);
  await step(`el.textContent && el.textContent.startsWith('Explore this county')`, '"Explore this county\'s census tracts"');
  await idle(page, 'county:36029');
  report.check((await text(page, '#scope-bar')).startsWith('Showing 261 census tracts in Erie County'), 'keyboard reached Erie County\'s tracts');
  report.check(await page.evaluate(() => document.activeElement.id) === 'scope-bar',
    'after the pressed control is replaced, focus lands on the scope line, not the page');
  await step(`el.id === 'measure-search'`, 'measure search', null, { back: true });
  await page.keyboard.type('Foreign-born share'); await page.waitForTimeout(150);
  await step(`el.classList && el.classList.contains('measure')`, 'first matching measure');
  await idle(page, 'county:36029');
  report.check((await text(page, '#measure-title')) === 'Foreign-born share of residents', 'keyboard chose a measure');
  await step(`el.closest && el.closest('#table-body') && el.tabIndex === 0`, 'the table (one tab stop)');
  await page.waitForTimeout(150);
  const firstPick = await page.evaluate(() => state.pick);
  await step(`el.textContent === 'Add to comparison'`, '"Add to comparison" for the first row');
  await step(`el.closest && el.closest('#table-body') && el.tabIndex === 0`, 'back to the table', null, { back: true });
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter'); await page.waitForTimeout(150);
  await step(`el.textContent === 'Add to comparison'`, '"Add to comparison" for the second row');
  const compare = await page.evaluate(() => state.compare);
  report.check(compare.length === 2 && compare[0] === firstPick, 'two places compared by keyboard', compare);
  report.check(await page.$$eval('.interval-chart .ic-row', (r) => r.length) === 2, 'the comparison chart is drawn');
  if (mode === 'static') {
    await step(`el.id === 'btn-share'`, 'Share view', 'Enter', { back: true });
    await page.waitForTimeout(150);
    const share = await page.evaluate(() => ({ focus: document.activeElement.id, value: document.getElementById('share-url').value }));
    report.check(share.focus === 'share-url' && share.value.includes('#view='), 'Share puts focus on the link to copy', share.focus);
  }
  const popup = page.context().waitForEvent('page', { timeout: 20000 }).catch(() => null);
  await step(`el.id === 'btn-brief'`, 'Open brief', 'Enter', { back: true });
  const brief = await popup;
  if (brief) {
    await brief.waitForLoadState('domcontentloaded').catch(() => {});
    const title = await brief.title().catch(() => '');
    report.check(/Foreign-born share/.test(title) || /Foreign-born share/.test(await brief.content().catch(() => '')), 'the brief opened for the measure on screen', title);
    await brief.close();
  } else {
    report.check(false, 'the brief opened in a new tab');
  }
  if (mode === 'static') {
    const download = page.waitForEvent('download', { timeout: 20000 }).catch(() => null);
    await step(`el.id === 'btn-export'`, 'Download CSV', 'Enter', { back: true });
    const d = await download;
    report.check(Boolean(d) && /county36029/.test(d.suggestedFilename()), 'the CSV downloads for Erie County\'s tracts', d && d.suggestedFilename());
  } else {
    await step(`el.id === 'btn-export'`, 'Export', 'Enter', { back: true });
    await page.waitForSelector('#export-result:not([hidden]) #export-files a', { timeout: 30000 }).catch(() => {});
    const files = await page.$$eval('#export-files li a:first-child', (as) => as.map((a) => a.textContent));
    report.check(files.includes('data.csv') && files.includes('brief.html'), 'the export bundle is written and linked', files);
  }
  await shot(page, `${mode}-390-keyboard-end`);
  await finish(app, `${mode} keyboard`);
}

/**
 * From the table to the result actions (Share, Open brief, Export) at 390 CSS
 * px: how many key presses by plain Tab order, and by the skip route when the
 * page offers one. Also checks where each skip link lands, that it is visible
 * when focused, the route back, that the URL is left alone, and that disabled
 * actions stay disabled and unfocusable while loading and after a failure.
 */
async function resultActionsRoute(mode, url) {
  report.section(`${mode}: from the table to the result actions at 390 CSS px`);
  const app = await openApp(mode, url, NARROW);
  app.expectFailure = true;
  const { page } = app;
  await page.click('#region-switch button[data-region="nys"]'); await idle(page, 'nys');
  await page.click('.level-switch button[data-level="tract"]'); await idle(page, 'nys');
  await page.selectOption('#county-select', '36029'); await idle(page, 'county:36029');
  const actions = mode === 'static'
    ? [['btn-share', 'Share view'], ['btn-brief', 'Open brief'], ['btn-export', 'Download CSV']]
    : [['btn-brief', 'Open brief'], ['btn-export', 'Export']];
  const toRow = async () => page.focus('#table-body tr[tabindex="0"]');
  const hashBefore = await page.evaluate(() => location.hash);

  // Plain Tab order, both directions. Forward only arrives by wrapping from
  // the end of the document to its start, which in a desktop browser also
  // passes through the browser's own toolbar; headless Chromium skips that.
  const measureNatural = async (label) => {
    const natural = {};
    for (const [id, name] of actions) {
      const counts = [];
      for (const back of [false, true]) {
        await toRow();
        counts.push(await tabTo(page, `el.id === '${id}'`, name, { back, max: 200 }));
      }
      natural[name] = { forward_wrapping: counts[0], backward: counts[1] };
    }
    report.note(`plain Tab order from the table, ${label} (key presses)`, natural);
  };
  await measureNatural('nothing inspected');
  // The usual state after comparing: one place inspected, two compared.
  const firstRows = await page.$$eval('#table-body tr[data-geoid]', (rs) => rs.slice(0, 2).map((r) => r.dataset.geoid));
  for (const g of firstRows) {
    await page.click(`#table-body tr[data-geoid="${g}"]`);
    await page.click('#place-card button:has-text("Add to comparison")');
  }
  await measureNatural('one place inspected, two compared');

  const hasRoute = await page.$('#table-skip-actions');
  if (!hasRoute) {
    report.check(false, 'a skip route from the table to the result actions exists');
    await finish(app, `${mode} result actions`);
    return;
  }

  // The skip route.
  const route = {};
  for (const [id, name] of actions) {
    await toRow();
    let presses = 0;
    await page.keyboard.press('Tab'); presses += 1;
    const link = await page.evaluate(() => {
      const el = document.activeElement; const r = el.getBoundingClientRect();
      return { id: el.id, text: el.textContent.trim(), visible: r.top >= 0 && r.bottom <= innerHeight && r.width > 0 };
    });
    if (id === actions[0][0]) {
      report.check(link.id === 'table-skip-actions' && link.visible,
        'one Tab from the table reaches a visible "Skip to result actions" link', link);
      report.check(await focusVisible(page) === true, 'the skip link shows a focus indicator');
    }
    await page.keyboard.press('Enter'); presses += 1;
    const landed = await page.evaluate(() => document.activeElement.id);
    if (id === actions[0][0]) report.check(landed === 'result-actions', 'Enter lands on the result actions group', landed);
    const n = await tabTo(page, `el.id === '${id}'`, name, { max: 10 });
    presses += n;
    route[name] = presses;
    report.check(presses <= 5 && await focusVisible(page) === true, `${name}: ${presses} key presses from the table by the skip route (target at most 5), focus visible`);
  }
  report.check(await page.evaluate(() => location.hash) === hashBefore, 'the skip links leave the page address alone', await page.evaluate(() => location.hash));

  // Back again, and the ordinary reverse direction.
  const row = await page.evaluate(() => document.querySelector('#table-body tr[tabindex="0"]').dataset.geoid);
  await page.focus('#result-actions');
  await page.keyboard.press('Shift+Tab');
  const before = await page.evaluate(() => document.activeElement.id);
  report.check(before === 'btn-method', 'Shift+Tab from the actions group goes to the control before it, not into a trap', before);
  await page.focus('#btn-export');
  await page.keyboard.press('Tab');
  const back = await page.evaluate(() => ({ id: document.activeElement.id, text: document.activeElement.textContent.trim() }));
  report.check(back.id === 'actions-skip-table', 'one Tab after the last action reaches "Back to the table"', back);
  await page.keyboard.press('Enter');
  const home = await page.evaluate(() => document.activeElement.dataset && document.activeElement.dataset.geoid);
  report.check(home === row, 'Enter returns focus to the table row that was left', { expected: row, got: home });

  // While loading: the actions are disabled and the route skips them.
  let release;
  const held = new Promise((r) => { release = r; });
  await page.route(/naturalized_share_of_foreign_born/, async (route_) => { await held; return route_.continue(); });
  // Started, not awaited: the load is held (or failed) on purpose.
  await page.evaluate(() => { chooseMeasure('naturalized_share_of_foreign_born'); });
  await page.waitForFunction(() => state.loading);
  await page.focus('#table-skip-actions'); await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');
  const loading = await page.evaluate((ids) => ({
    focus: document.activeElement.id,
    disabled: ids.map((i) => document.getElementById(i).disabled),
  }), actions.map(([i]) => i));
  report.check(loading.disabled.every(Boolean) && !actions.some(([i]) => i === loading.focus),
    'while a view loads, every result action is disabled and Tab from the group skips them', loading);
  release(); await idle(page, 'county:36029');
  await page.unroute(/naturalized_share_of_foreign_born/);

  // After a failed load: still disabled, and the way back still lands somewhere real.
  await page.route(/foreign_born_share/, (r) => (/values/.test(r.request().url()) ? r.abort() : r.continue()));
  // Started, not awaited: the load is held (or failed) on purpose.
  await page.evaluate(() => { chooseMeasure('foreign_born_share'); });
  await page.waitForSelector('#blocked:not([hidden])', { timeout: 20000 });
  await page.focus('#result-actions'); await page.keyboard.press('Tab');
  const failed = await page.evaluate((ids) => ({
    focus: document.activeElement.id, disabled: ids.map((i) => document.getElementById(i).disabled),
    saveSubmit: document.getElementById('save-submit') ? document.getElementById('save-submit').disabled : null,
  }), actions.map(([i]) => i));
  // Locally, "Saved views" stays usable so saved views can still be reopened;
  // its Save button is what waits for a complete view.
  const next = mode === 'static' ? 'actions-skip-table' : 'btn-save';
  report.check(failed.disabled.every(Boolean) && failed.focus === next && (mode === 'static' || failed.saveSubmit === true),
    `after a failed load share/brief/export stay disabled; Tab from the group reaches ${mode === 'static' ? '"Back to the table"' : '"Saved views", whose Save stays disabled'}`, failed);
  await page.focus('#actions-skip-table');
  await page.keyboard.press('Enter');
  const fallback = await page.evaluate(() => document.activeElement.id);
  report.check(fallback === 'table-title', 'with no rows, "Back to the table" lands on the table heading', fallback);
  await page.unroute(/foreign_born_share/);
  report.note('skip route (key presses from the table)', route);
  await finish(app, `${mode} result actions`);
}

/* ------------------------------------------------------ hospital layer */

/** Everything the census view shows, to prove the hospital layer leaves it alone. */
async function acsFingerprint(page) {
  return page.evaluate(() => ({
    scope: state.scope, level: state.level, measure: state.measureId, pick: state.pick,
    compare: [...state.compare], hash: location.hash,
    paths: document.querySelectorAll('#map-layer > path').length,
    fills: [...document.querySelectorAll('#map-layer > path')].map((p) => p.getAttribute('fill')).join('|'),
    table: document.getElementById('table-body').textContent.replace(/\s+/g, ' ').trim(),
    scopeBar: document.getElementById('scope-bar').textContent.replace(/\s+/g, ' ').trim(),
    legend: document.getElementById('legend').textContent.replace(/\s+/g, ' ').trim(),
  }));
}

const hospMarks = (page) => page.$$eval('#hosp-layer .hosp-mark', (els) => els.map((e) => e.dataset.fac));

/**
 * The optional hospital layer: off by default and loads nothing; county
 * filter; map, table and CSV describe the same records; an unlocated site
 * stays listed with its reason; main-site links; CMS ratings only on CMS
 * entities, with footnotes; turning the layer off restores the census view.
 */
async function hospitalLayer(mode, url) {
  report.section(`${mode}: hospital layer`);
  const app = await openApp(mode, url, DESKTOP);
  const { page } = app;
  const hospRequests = [];
  page.on('request', (r) => { if (/hospitals/.test(r.url())) hospRequests.push(r.url()); });
  await page.click('#region-switch button[data-region="nys"]'); await idle(page, 'nys');
  const before = await acsFingerprint(page);
  report.check(await page.isVisible('#hosp-toggle') && !(await page.isChecked('#hosp-toggle'))
    && (await hospMarks(page)).length === 0 && hospRequests.length === 0,
  'the layer is offered, off by default, and nothing hospital-related is requested or drawn',
  { requests: hospRequests.length });

  await page.check('#hosp-toggle');
  await page.waitForSelector('#hosp-summary', { timeout: 60000 });
  const reg = await page.evaluate(() => HospitalLayer._state.reg);
  const sites = reg.sites;
  report.check(sites.length === reg.reports.counts.hospital_family_sites && reg.cms_entities.length === reg.reports.counts.cms_ny_rows,
    'the registry loads whole', `${sites.length} sites, ${reg.cms_entities.length} CMS entities, retrieval ${reg.retrieval_manifest_id}`);
  report.check(reg.reports.checks.every((c) => c.passed), 'every reconciliation check in the loaded registry passed',
    reg.reports.checks.map((c) => c.check_id));
  report.check(reg.data_mode === 'live' && (await page.$('.hosp-fixture')) === null,
    'the registry is live data and carries no synthetic-data banner',
    `data mode ${reg.data_mode}; rules v${reg.rules_version} ${String(reg.rules_sha256).slice(0, 12)}`);

  // A county with at least one unlocated site, so "missing location" is exercised.
  const byCounty = {};
  sites.forEach((s) => { (byCounty[s.county_fips] ||= []).push(s); });
  const county = Object.keys(byCounty).filter((c) => byCounty[c].some((s) => s.location_status !== 'valid'))
    .sort((a, b) => byCounty[a].length - byCounty[b].length)[0];
  const expected = byCounty[county];
  const expectedLocated = expected.filter((s) => s.location_status === 'valid').map((s) => s.fac_id).sort();
  await page.selectOption('#hosp-county', county);
  await page.waitForTimeout(150);
  const marks = (await hospMarks(page)).sort();
  const summary = await text(page, '#hosp-summary');
  report.check(JSON.stringify(marks) === JSON.stringify(expectedLocated),
    `county filter (${expected[0].county_name}, ${county}): the map draws exactly its located sites`,
    `${marks.length} markers; ${expected.length} sites, ${expected.length - expectedLocated.length} unlocated`);
  report.check(summary.startsWith(`${expected.length.toLocaleString('en-US')} of ${sites.length.toLocaleString('en-US')} sites match.`)
    && summary.includes(`${expectedLocated.length.toLocaleString('en-US')} are on the map`)
    && summary.includes(`${(expected.length - expectedLocated.length).toLocaleString('en-US')} have no published location`),
  'the directory summary states the same counts', summary);
  const rows = await page.$$eval('#hosp-rows button[data-fac]', (b) => b.map((x) => x.dataset.fac));
  report.check(rows.length === Math.min(25, expected.length) && rows.every((f) => expected.some((s) => s.fac_id === f)),
    'the table lists only that county\'s sites', `${rows.length} rows on page 1`);

  const [download] = await Promise.all([page.waitForEvent('download'), page.click('#hosp-csv')]);
  const csv = fs.readFileSync(await download.path(), 'utf8').trim().split('\r\n');
  const csvIds = csv.slice(1).map((l) => l.split(',')[0]).sort();
  report.check(csvIds.length === expected.length
    && JSON.stringify(csvIds) === JSON.stringify(expected.map((s) => s.fac_id).sort()),
  'the CSV holds exactly the filtered records, unlocated ones included', `${csvIds.length} rows, ${download.suggestedFilename()}`);
  // Every bed record survives the export whole: the CSV cell parses back to the
  // registry's records for that site.
  const header = csv[0].split(',');
  const bedCol = header.indexOf('certified_bed_records_json');
  const parseLine = (line) => {
    const out = []; let cur = ''; let q = false;
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i];
      if (q) { if (ch === '"' && line[i + 1] === '"') { cur += '"'; i += 1; } else if (ch === '"') q = false; else cur += ch; }
      else if (ch === '"') q = true; else if (ch === ',') { out.push(cur); cur = ''; } else cur += ch;
    }
    out.push(cur); return out;
  };
  const bedsOk = csv.slice(1).every((line) => {
    const cells = parseLine(line);
    const s = expected.find((x) => x.fac_id === cells[0]);
    return cells.length === header.length && JSON.stringify(JSON.parse(cells[bedCol])) === JSON.stringify(s.certified_beds);
  });
  report.check(bedCol >= 0 && bedsOk && !header.some((h) => /total|capacity/i.test(h)),
    'each CSV row carries its site\'s bed records whole (category, count, sub type, dates), and no total',
    `${expected.reduce((n, s) => n + s.certified_beds.length, 0)} bed records; ${csvIds.length} rows`);

  // An unlocated site: listed, reason shown, not drawn.
  const unlocated = expected.find((s) => s.location_status !== 'valid');
  await page.fill('#hosp-q', unlocated.fac_id); await page.waitForTimeout(150);
  await page.click(`#hosp-rows button[data-fac="${unlocated.fac_id}"]`); await page.waitForTimeout(150);
  const det = await text(page, '#hosp-detail');
  report.check(det.includes('Not mapped.') && det.includes(unlocated.location_reason)
    && det.includes('stays in the directory and the CSV') && !(await hospMarks(page)).includes(unlocated.fac_id),
  'an unlocated site shows its reason and is not drawn', `${unlocated.fac_id}: ${unlocated.location_reason}`);
  await page.fill('#hosp-q', ''); await page.selectOption('#hosp-county', '');

  // A hospital-operated site links to its main site; the main site lists it back.
  const op = sites.find((s) => s.type_group === 'extension' && s.main_site_status === 'listed' && s.location_status === 'valid');
  await page.fill('#hosp-q', op.fac_id); await page.waitForTimeout(150);
  await page.click(`#hosp-rows button[data-fac="${op.fac_id}"]`); await page.waitForTimeout(150);
  const opText = await text(page, '#hosp-detail');
  await page.click(`#hosp-detail button[data-open-fac="${op.main_site_fac_id}"]`); await page.waitForTimeout(150);
  const mainText = await text(page, '#hosp-detail');
  report.check(opText.includes('Hospital extension site') && opText.includes(`HFIS ${op.main_site_fac_id}`)
    && mainText.includes(`${op.main_site_name}`) && mainText.includes('list this as their main site'),
  'an extension site links to its main site, which lists the sites under it', `${op.fac_id} → ${op.main_site_fac_id}`);
  report.check(!/star rating/i.test(mainText) && !/of 5\b/.test(mainText),
    'no CMS rating is shown on an HFIS site');
  await page.fill('#hosp-q', '');

  // A marker opens its site and leaves the census selection alone.
  const pickBefore = await page.evaluate(() => state.pick);
  const mark = await page.$(`#hosp-layer .hosp-mark[data-fac="${op.fac_id}"]`);
  if (mark) {
    await page.click('#zoom-fit');
    const box = await mark.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    await page.waitForTimeout(200);
    const picked = await page.evaluate(() => HospitalLayer._state.pick);
    // Several markers can share an address; the one drawn on top opens.
    report.check(typeof picked === 'string' && (await page.evaluate(() => state.pick)) === pickBefore,
      'clicking a marker opens a site and does not change the census selection', `opened ${picked}`);
  } else {
    report.check(false, 'the chosen located extension site has a marker', op.fac_id);
  }

  // CMS entities: ratings with their footnotes, unavailable is a reason.
  await page.click('#hosp-tab-cms'); await page.waitForTimeout(100);
  const na = reg.cms_entities.find((e) => e.overall_rating_status === 'not_available' && e.overall_rating_footnotes.length);
  const rated = reg.cms_entities.find((e) => e.overall_rating_status === 'rated');
  const openEnt = async (e) => {
    await page.evaluate((c) => HospitalLayer.openEntity(c, { focus: true }), e.ccn);
    await page.waitForTimeout(120);
    return text(page, '#hosp-detail');
  };
  const naText = await openEnt(na);
  report.check(naText.includes('Not available') && naText.includes(na.overall_rating_footnotes[0].text)
    && !/\b0 of 5\b/.test(naText), 'an unrated CMS entity shows "Not available" with the CMS footnote', `${na.ccn}: footnote ${na.overall_rating_footnotes[0].code}`);
  const ratedText = await openEnt(rated);
  const href = await page.$eval('#hosp-detail a[href*="medicare.gov"]', (a) => a.href).catch(() => null);
  report.check(ratedText.includes(`${rated.overall_rating} of 5`) && ratedText.includes('Not a rating of any one HFIS site')
    && href === `https://www.medicare.gov/care-compare/details/hospital/${rated.ccn}`,
  'a rated entity shows its CMS rating as an entity-level value, with the Care Compare link by CCN', `${rated.ccn}: ${href}`);
  const amb = reg.cms_entities.find((e) => e.hfis_match_state === 'ambiguous');
  const ambText = amb ? await openEnt(amb) : '';
  report.check(Boolean(amb) && ambText.includes('HFIS site candidates: Ambiguous') && !/confirmed match/i.test(ambText.replace('None is a confirmed match', '')),
    'an ambiguous CCN is shown as ambiguous, with its evidence, never as a match', amb && amb.ccn);
  await shot(page, `${mode}-hospitals-cms`);

  // Off again: the census view is exactly as it was.
  await page.uncheck('#hosp-toggle'); await page.waitForTimeout(150);
  const after = await acsFingerprint(page);
  report.check((await hospMarks(page)).length === 0 && await page.isHidden('#hospitals'),
    'turning the layer off removes every marker and the directory');
  report.check(JSON.stringify(before) === JSON.stringify(after),
    'with the layer off the census map, table, legend, scope and address are unchanged',
    `${after.paths} shapes, scope ${after.scope}`);
  await finish(app, `${mode} hospital layer`);
}

/** Keyboard only at 390 CSS px: turn the layer on, filter, open a site, export. */
async function hospitalKeyboard(mode, url) {
  report.section(`${mode}: hospital layer, keyboard only at 390 CSS px`);
  const app = await openApp(mode, url, NARROW);
  const { page } = app;
  const presses = {};
  presses.toggle = await tabTo(page, "el.id === 'hosp-toggle'", 'the hospital layer toggle');
  await page.keyboard.press('Space');
  await page.waitForSelector('#hosp-summary', { timeout: 60000 });
  report.check(await page.isChecked('#hosp-toggle'), 'Space turns the layer on');
  presses.search = await tabTo(page, "el.id === 'hosp-q'", 'the hospital search');
  const name = await page.evaluate(() => HospitalLayer._state.reg.sites.find((s) => s.type_group === 'hospital' && s.certified_beds.some((b) => b.beds !== null)).name);
  await page.keyboard.type(name.slice(0, 14));
  await page.waitForTimeout(150);
  presses.county = await tabTo(page, "el.id === 'hosp-county'", 'the county filter');
  presses.row = await tabTo(page, "el.matches('#hosp-rows button[data-fac]')", 'the first directory row');
  report.check(await focusVisible(page) === true, 'focus is visible on the directory row', await focusVisible(page));
  await page.keyboard.press('Enter'); await page.waitForTimeout(250);
  const active = await page.evaluate(() => document.activeElement.id);
  report.check(active === 'hosp-detail', 'Enter opens the details and moves focus to them', active);
  await page.waitForFunction(() => !/Loading…/.test(document.getElementById('hosp-cert').textContent), null, { timeout: 30000 });
  const det = await text(page, '#hosp-detail');
  report.check(/Certified beds/.test(det) && /never added up/.test(det) && !/total beds/i.test(det),
    'certified beds are listed by category and never totalled');
  presses.csv = await tabTo(page, "el.id === 'hosp-csv'", 'the CSV download', { back: true });
  const [download] = await Promise.all([page.waitForEvent('download'), page.keyboard.press('Enter')]);
  const n = fs.readFileSync(await download.path(), 'utf8').trim().split('\r\n').length - 1;
  const expected = await page.evaluate(() => HospitalLayer._state.filtered.length);
  report.check(n === expected, 'Enter on the CSV button downloads exactly the filtered sites', `${n} rows`);
  report.check(await page.evaluate(() => document.activeElement !== document.body), 'focus never fell back to the page body');
  report.note('Tab presses to each stop', presses);
  await layoutAudit(page, 'hospital directory with details open');
  await page.$eval('#hosp-detail', (el) => el.scrollIntoView());
  await shot(page, `${mode}-390-hospital-detail`);
  await finish(app, `${mode} hospital keyboard`);
}

/* ------------------------------------------------------------ run */

const targets = [['local', args.local], ['static', args.static]].filter(([, u]) => u);
// --only <name> runs one journey, for diagnosis; a release run runs them all.
const JOURNEYS = {
  'county-to-tract': countyToTract, comparison: outsideComparison,
  share: (mode, url) => (mode === 'static' ? shareAndReload(url) : null),
  failure: loadFailure, layout: narrowLayout, keyboard: keyboardOnly, 'result-actions': resultActionsRoute,
  hospitals: hospitalLayer, 'hospitals-keyboard': hospitalKeyboard,
};
if (args.only && !JOURNEYS[args.only]) {
  console.error(`--only must be one of: ${Object.keys(JOURNEYS).join(', ')}`); process.exit(2);
}
for (const [mode, url] of targets) {
  for (const [name, run] of Object.entries(JOURNEYS)) {
    if (!args.only || args.only === name) await run(mode, url);
  }
}
await browser.close();
const body = report.write({
  run_at: new Date().toISOString(),
  targets: Object.fromEntries(targets),
  browser: 'Chromium via Playwright (headless shell)',
  only: args.only || 'all journeys',
  desktop_viewport: `${DESKTOP.width}×${DESKTOP.height}`,
  narrow_viewport: `${NARROW.width}×${NARROW.height}`,
});
console.log(`\n${body.failures} failing check(s). Report: ${path.join(dir, 'report.md')}`);
process.exit(body.failures ? 1 : 0);
