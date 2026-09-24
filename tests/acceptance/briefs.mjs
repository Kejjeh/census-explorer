// Printed-brief acceptance: render briefs to PDF and look at every page.
//
//   node tests/acceptance/briefs.mjs \
//     --local  http://127.0.0.1:8765/ \
//     --static http://127.0.0.1:8899/census-explorer/ \
//     [--out artifacts/acceptance/briefs-<stamp>]
//
// Two briefs from each target, on the live 2019-2023 build:
//   borough  - the Bronx, naturalised share of the foreign-born, with the New
//              York City reference;
//   tracts   - New York City's census tracts, Dominican-Republic share of the
//              foreign-born, whose table lists 25 of 2,327 rows.
//
// Each brief is printed to an A4 PDF by Chromium's print engine (what "Save
// as PDF" in its print dialog uses), and then every page of that PDF is
// opened in Chromium's own PDF viewer and saved as a PNG for a person to
// inspect. The PNGs are the visual evidence; the numbers this script
// prints (pages, clipped cells) are checks, not acceptance on their own.
//
// The published brief is captured from the page exactly as "Open brief"
// builds it: the blob the page creates is read back instead of opening a
// blob tab, which headless Chromium does not let a script inspect.

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { loadPlaywright, parseArgs, outDir, createReport } from './lib.mjs';

const args = parseArgs(process.argv.slice(2), { local: '', static: '', out: '' });
if (!args.local && !args.static) { console.error('give --local and/or --static'); process.exit(2); }
const { chromium } = await loadPlaywright();
const dir = outDir(args.out, 'briefs');
const report = createReport(dir, 'Census Explorer printed briefs (live data)');

const CASES = {
  borough: { measure: 'naturalized_share_of_foreign_born', level: 'county', scope: 'nyc', areas: ['36005'], benchmark: 'nyc',
    expect: ['Bronx', 'New York City', 'foreign-born'] },
  tracts: { measure: 'fb_dominican_republic_share_of_foreign_born', level: 'tract', scope: 'nyc', areas: [], benchmark: 'none',
    expect: ['Dominican Republic', '25'] },
};

// Print with the headless shell; view with the full build, which carries the PDF viewer.
const printer = await chromium.launch();
const viewer = await chromium.launch({ channel: 'chromium' });

async function localHtml(base, c) {
  const q = new URLSearchParams({ release: 'acs5_2023', measure: c.measure, level: c.level,
    scope: c.scope, benchmark: c.benchmark });
  if (c.areas.length) q.set('areas', c.areas.join(','));
  const res = await fetch(new URL(`api/brief?${q}`, base));
  if (!res.ok) throw new Error(`brief request failed: ${res.status}`);
  return res.text();
}

async function staticHtml(base, c) {
  const page = await printer.newPage({ viewport: { width: 1480, height: 1000 } });
  await page.goto(base);
  await page.waitForSelector('#map svg path', { timeout: 60000 });
  await page.waitForFunction(() => state.ready && !state.loading);
  const mode = await page.evaluate(() => state.status.data_mode);
  if (mode !== 'live') { console.error(`data_mode=${mode}; a live build is required`); process.exit(2); }
  // The selection is made through the page's own load path.
  await page.evaluate(async (sel) => {
    state.level = sel.level; state.scope = sel.scope; state.measureId = sel.measure;
    state.areas = [...sel.areas]; state.benchmarkId = sel.benchmark;
    renderLevelSwitch(); renderSidebar();
    await loadBenchmarks(); await refresh();
  }, c);
  await page.waitForFunction(() => state.ready && !state.loading);
  await page.evaluate(() => {
    window.__brief = null;
    const create = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (blob) => { blob.text().then((t) => { window.__brief = t; }); return create(blob); };
    window.open = () => null;
  });
  await page.click('#btn-brief');
  await page.waitForFunction(() => window.__brief !== null, null, { timeout: 20000 });
  const html = await page.evaluate(() => window.__brief);
  await page.close();
  return html;
}

async function inspect(label, html, c) {
  const base = path.join(dir, label);
  fs.writeFileSync(`${base}.html`, html);
  const page = await printer.newPage({ viewport: { width: 1000, height: 1400 } });
  await page.setContent(html, { waitUntil: 'load' });
  await page.emulateMedia({ media: 'print' });
  // Layout checks under print styles, at the A4 content width (210 mm - 2 x 16 mm).
  await page.setViewportSize({ width: 673, height: 1000 });
  const layout = await page.evaluate(() => {
    const clipped = [...document.querySelectorAll('th, td')]
      .filter((el) => el.scrollWidth > el.clientWidth + 1)
      .map((el) => el.textContent.trim().slice(0, 40));
    const wide = [...document.querySelectorAll('table, svg, pre, img, figure')]
      .filter((el) => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
      .map((el) => el.tagName);
    return {
      headings: [...document.querySelectorAll('h1, h2')].map((h) => h.textContent.trim()),
      rows: document.querySelectorAll('tbody tr').length,
      clipped, wide, overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      text: document.body.innerText,
    };
  });
  report.check(layout.overflow <= 0 && layout.wide.length === 0, `${label}: nothing wider than the printed page`, layout.wide.length ? layout.wide : `overflow ${layout.overflow}px`);
  report.check(layout.clipped.length === 0, `${label}: no table cell clips its text`, layout.clipped.length ? layout.clipped.slice(0, 5) : undefined);
  const lower = layout.text.toLowerCase();
  const needs = {
    denominator: /out of|denominator|percentage of|share of/.test(lower),
    uncertainty: /margin of error|±|percentage points/.test(lower),
    source: /census bureau|american community survey/.test(lower),
    period: /2019-2023|2019–2023/.test(layout.text),
    ...Object.fromEntries(c.expect.map((e) => [e, layout.text.includes(e)])),
  };
  report.check(Object.values(needs).every(Boolean), `${label}: denominator, uncertainty, source and period are printed`, needs);
  report.note(`${label}: headings`, layout.headings);
  const pdfPath = `${base}.pdf`;
  await page.pdf({ path: pdfPath, format: 'A4', preferCSSPageSize: true, printBackground: true });
  await page.close();
  const bytes = fs.readFileSync(pdfPath).toString('latin1');
  const pages = (bytes.match(/\/Type\s*\/Page(?![s\w])/g) || []).length;
  report.note(`${label}: PDF written`, `${path.basename(pdfPath)}, ${pages} page(s)`);

  // Every page, as the PDF viewer draws it.
  // A fresh tab per page: the viewer ignores a change to #page= within an
  // open document, which would silently photograph page 1 every time.
  const shots = [];
  for (let n = 1; n <= pages; n += 1) {
    const v = await viewer.newPage({ viewport: { width: 860, height: 1160 } });
    await v.goto(`file://${pdfPath}#page=${n}&toolbar=0&navpanes=0&zoom=100`);
    await v.waitForTimeout(1500);
    const png = `${base}-page${n}.png`;
    await v.screenshot({ path: png });
    shots.push(path.basename(png));
    await v.close();
  }
  report.check(shots.length === pages && pages > 0, `${label}: every page rendered by the PDF viewer for inspection`, shots);
  const hashes = new Set(shots.map((f) => crypto.createHash('sha256').update(fs.readFileSync(path.join(dir, f))).digest('hex')));
  report.check(hashes.size === shots.length, `${label}: each page image is a different page`, `${hashes.size} distinct of ${shots.length}`);
  return { pdf: path.basename(pdfPath), pages, shots };
}

const results = {};
for (const [mode, base] of [['local', args.local], ['static', args.static]]) {
  if (!base) continue;
  report.section(`${mode} briefs`);
  for (const [name, c] of Object.entries(CASES)) {
    const html = mode === 'local' ? await localHtml(base, c) : await staticHtml(base, c);
    results[`${mode}-${name}`] = await inspect(`${mode}-${name}`, html, c);
  }
}
await printer.close();
await viewer.close();
const body = report.write({
  run_at: new Date().toISOString(),
  targets: { local: args.local || null, static: args.static || null },
  paper: 'A4, the brief\'s own @page margins',
  printer: 'Chromium print-to-PDF (headless shell)',
  viewer: 'Chromium PDF viewer (full Chromium, headless)',
  results,
  visual_inspection: 'required: open the page PNGs; this script does not judge them',
});
console.log(`\n${body.failures} failing check(s). Report: ${path.join(dir, 'report.md')}`);
process.exit(body.failures ? 1 : 0);
