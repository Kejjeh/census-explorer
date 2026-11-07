// Shared helpers for the live browser acceptance runs in this directory.
//
// These scripts are NOT part of the offline test suite. They drive a real
// browser against a real, already-built dataset: the local service and the
// published static site. See docs/ACCEPTANCE.md for the recipe.
//
// No dependency is added to this repository. Playwright is loaded from where
// it is already installed: $PLAYWRIGHT_MODULE if set, else the global Node
// install used in the reference environment. If neither exists the script
// stops and says so; nothing is downloaded.

import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const CANDIDATES = [
  process.env.PLAYWRIGHT_MODULE,
  '/opt/node22/lib/node_modules/playwright/index.mjs',
].filter(Boolean);

export async function loadPlaywright() {
  for (const candidate of CANDIDATES) {
    if (fs.existsSync(candidate)) return import(pathToFileURL(candidate).href);
  }
  console.error('Playwright was not found. Set PLAYWRIGHT_MODULE to the path of an ' +
    'installed playwright/index.mjs. This repository does not install it.');
  process.exit(2);
}

/**
 * Where each kind of target publishes its build's status: the local service
 * answers /api/status; a static site ships the same record as a file.
 */
export const STATUS_PATH = { local: 'api/status', static: 'data/status.json' };

/**
 * Whether a status record says the build is live. Anything else fails
 * closed: fixture, a missing or unknown mode, or no record at all.
 */
export function liveModeVerdict(status) {
  if (!status || typeof status !== 'object' || Array.isArray(status)) {
    return { ok: false, reason: 'the status response is not a JSON object' };
  }
  if (!('data_mode' in status)) return { ok: false, reason: 'the status response has no data_mode' };
  if (status.data_mode !== 'live') {
    return { ok: false, reason: `data_mode is ${JSON.stringify(status.data_mode)}, not "live"` };
  }
  return { ok: true, reason: 'data_mode is "live"' };
}

/**
 * Read a target's own status metadata before anything is rendered or
 * reported, and refuse it unless it is a live build. Returns the verdict;
 * never throws, so the caller decides how to stop.
 */
export async function checkLiveTarget(kind, base) {
  const where = new URL(STATUS_PATH[kind], base).href;
  let res;
  try {
    res = await fetch(where, { headers: { Accept: 'application/json' } });
  } catch (e) {
    return { ok: false, where, reason: `could not reach it (${e.message})` };
  }
  if (!res.ok) return { ok: false, where, reason: `HTTP ${res.status}` };
  let status;
  try { status = JSON.parse(await res.text()); } catch (_) {
    return { ok: false, where, reason: 'the response is not JSON' };
  }
  return { where, ...liveModeVerdict(status) };
}

/**
 * Check every target; print one line per target; exit 2 on any refusal.
 * Runs before Playwright is loaded, before the output directory exists and
 * before any report is written, so a refused target leaves nothing behind
 * that could be mistaken for live evidence.
 */
export async function requireLiveTargets(targets) {
  let refused = 0;
  for (const [kind, base] of targets) {
    const v = await checkLiveTarget(kind, base);
    console.log(`${v.ok ? 'live' : 'REFUSED'}  ${kind} ${base}  (${v.where}: ${v.reason})`);
    if (!v.ok) refused += 1;
  }
  if (refused) {
    console.error('This acceptance run needs live builds. Fixture behaviour is tested ' +
      'offline in tests/; nothing was rendered or reported.');
    process.exit(2);
  }
}

export function parseArgs(argv, defaults) {
  const out = { ...defaults };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i].replace(/^--/, '');
    if (!(key in defaults)) throw new Error(`unknown option --${key}`);
    if (typeof defaults[key] === 'boolean') { out[key] = true; continue; }
    out[key] = argv[i + 1];
    i += 1;
  }
  return out;
}

export function outDir(requested, kind) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '').slice(0, 15);
  const dir = path.resolve(requested || path.join('artifacts', 'acceptance', `${kind}-${stamp}`));
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/** A report that records every check as it happens, pass or fail. */
export function createReport(dir, title) {
  const checks = [];
  let section = '';
  return {
    dir,
    section(name) { section = name; console.log(`\n## ${name}`); },
    check(ok, what, detail) {
      checks.push({ section, ok: Boolean(ok), what, detail: detail ?? null });
      console.log(`${ok ? 'PASS' : 'FAIL'}  ${what}${detail !== undefined ? `  — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
    },
    note(what, detail) {
      checks.push({ section, ok: null, what, detail: detail ?? null });
      console.log(`note  ${what}${detail !== undefined ? `  — ${typeof detail === 'string' ? detail : JSON.stringify(detail)}` : ''}`);
    },
    get failures() { return checks.filter((c) => c.ok === false).length; },
    write(meta) {
      const body = { title, ...meta, checks, failures: checks.filter((c) => c.ok === false).length };
      fs.writeFileSync(path.join(dir, 'report.json'), JSON.stringify(body, null, 2));
      const lines = [`# ${title}`, '', ...Object.entries(meta).map(([k, v]) => `- ${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`), ''];
      let last = null;
      for (const c of checks) {
        if (c.section !== last) { lines.push('', `## ${c.section}`, ''); last = c.section; }
        const mark = c.ok === null ? 'note' : (c.ok ? 'PASS' : 'FAIL');
        lines.push(`- ${mark} ${c.what}${c.detail !== null ? ` — ${typeof c.detail === 'string' ? c.detail : JSON.stringify(c.detail)}` : ''}`);
      }
      fs.writeFileSync(path.join(dir, 'report.md'), lines.join('\n') + '\n');
      return body;
    },
  };
}
