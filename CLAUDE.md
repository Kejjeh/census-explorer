# Agent guide

@AGENTS.md

`AGENTS.md` (imported above) holds the review rules for census data here. They apply in full and win over anything below.

## What this is

A local, offline-first tool that turns a question about a place into a print-ready brief from
official census data: 2019-2023 ACS five-year, every New York State county and census tract
(New York City is the default five-borough view), with definitions, denominators, margins of
error and provenance attached. Python 3.11+, **standard library only** (no third-party packages,
no CDN); the browser UI is plain HTML/CSS/JS in `web/`. **Current state: working.** The census-only
explorer is published on GitHub Pages (`gh-pages` `eb96a10`, built from `efc9ea1`). Hospital
Explorer phase 1 is built and checked locally but **not published**; it waits for independent
acceptance (`docs/HOSPITALS.md`, `docs/RELEASE_CHECKLIST.md`). There is no HANDOFF.md or glossary;
`docs/DATA_HANDLING.md` is the record of data decisions.

## Commands (verified from repo root, Windows)

```bash
python -m pytest -q                                   # ~50s. Expect: 517 passed, 6 skipped, 48 subtests passed
python -B -m unittest discover -s tests -t .          # what CI runs. Expect: Ran 523 tests ... OK (skipped=6)
python -m census_explorer.cli fixtures build          # offline; writes data/fixture-processed/
python -m census_explorer.cli serve --data-dir data/fixture-processed   # fixture mode at http://127.0.0.1:8765/
```

- The 6 skips on Windows: 3 symlink tests (platform cannot create symlinks) and 3 live smoke
  tests (need `CENSUS_EXPLORER_LIVE=1`, one also `CENSUS_API_KEY`).
- **Run the Node tests through pytest/unittest, not by hand.** `tests/js/*.test.js` are launched by
  `test_ui_core.py`, `test_hospitals.py` and `test_site_build.py`; the round-trip test needs
  `CENSUS_EXPLORER_ROUNDTRIP`, which `test_site_build.py` builds and sets. They skip if `node` is missing.
- **Network commands (explicit, keyless, free; large downloads):** `fetch all --release acs5_2023`
  (~435 MB of tables + ~92 MB roster), `reconcile --fetch`, `reference refresh`, `hospitals fetch`,
  `smoke`. Never run them unless the task needs a new retrieval.
- **Not verified on this machine (no `data/` cache here):** `build`, `serve` on real data,
  `verify manifests|catalog`, `export`, and `site build --base /census-explorer/ --out site`.
  They need a completed `fetch all` + `build`; see `docs/USAGE.md` and `docs/RUNBOOK.md` for expected output.

## Architecture map

| Path | Role |
|---|---|
| `census_explorer/cli.py` | CLI entry point (`python -m census_explorer.cli <command>`) |
| `census_explorer/retrieve/` | the **only** modules that touch the network (Summary File, API, boundaries, roster, hospitals) |
| `pipeline.py`, `dataset.py`, `measures.py`, `metadata.py` | build the analysis dataset from the cache; resolve cells from official metadata |
| `sentinels.py`, `reference/` | Census annotation codes as meanings, never numbers |
| `geography.py`, `shapefile.py`, `scope.py`, `selection.py` | string GEOIDs, join accounting, scope, one validated selection |
| `compare.py`, `benchmark.py`, `reconcile.py` | comparison eligibility (refuse by default), benchmarks, published-row reconciliation |
| `provenance.py`, `snapshot.py`, `projects.py`, `exports.py`, `brief.py` | manifests, pinned saved projects, CSV/provenance/figure bundles, briefs |
| `server.py` | local loopback service; switches network off for its own process |
| `site.py` | static build for GitHub Pages (computes once; the page computes nothing) |
| `hospitals.py`, `retrieve/hospitals.py`, `config/hospital*.json` | Hospital Explorer phase 1 registry |
| `config/` | project scope, releases, measure catalog (`measures.json`), label semantics |
| `web/` | browser UI (`app.js`, `core.js`, `hospitals.js`, `publish.js`, `static.js`) |
| `tests/`, `fixtures/` | offline suite; small synthetic inputs |

## Conventions and gotchas

- **Publishing is a separate `gh-pages` checkout, never `main`.** The reviewed path builds
  `site/` locally and copies it into a temporary worktree `../census-explorer-pages` on `gh-pages`
  (`docs/DEPLOY.md`). `.github/workflows/pages.yml` is manual and does its own new retrieval, so it is
  not the reviewed build. Publishing needs the owner's go-ahead; don't touch `gh-pages` otherwise.
- Network access is always an explicit command. Imports, tests, build, serve and figure rendering
  must never fetch; `tests/` must stay offline.
- Unavailable is not zero; missing uncertainty is unavailable; period labels are never shortened
  ("2019-2023 ACS"); comparisons across boundary vintages are blocked. The README's
  "What the interface insists on" list is the contract; tests assert most of it.
- Fixture mode is synthetic and banner-marked. Nothing from it is a census finding.
- Git-ignored, generated, do not commit or hand-edit: `data/` (raw cache, manifests, processed
  datasets, projects), `artifacts/` (exports, release candidates), `site/`.
- Committed digests (`docs/*.sha256`) describe a specific release; don't regenerate them casually.
- Secrets: `CENSUS_API_KEY` (optional `--transport api` only) and `IPUMS_API_KEY` come from the
  environment; `.env` is git-ignored, `.env.example` lists names only. Never paste or commit a key.
- Set `PYTHONUTF8=1` on Windows if output encoding errors appear.
- Don't read into context: `data/`, `artifacts/`, `site/`, `docs/*.sha256`. `web/app.js` (2,700 lines)
  and `server.py` (1,650): search them, don't read whole.

## Skills for this repo

Load these with the Skill tool when the situation comes up; the user does not need to ask.

| Situation in this repo | Skill |
|---|---|
| A test fails, `reconcile` stops matching, a map/table/CSV value or count looks wrong | `diagnosing-bugs` |
| Writing or reviewing retrieval, measures, sentinels, joins or hospital matching, where a silent wrong number, dropped row or duplicate join is the risk | `sop-fail-loud` |
| A fix needs a methodology call: a denominator, boundary equivalence, comparison eligibility, a hospital conflict rule, which release to publish | `sop-owner-decision` |
| A change alters the static site, a release candidate, or committed `docs/*.sha256` digests | `sop-regenerate-outputs` |
| Adding a measure or fixing a data bug test-first (the offline suite is the safety net) | `tdd` |
| Proving a UI change works in the running app (fixture mode, `serve --data-dir data/fixture-processed`) | `run` |
| Changing the map legend, comparison chart or SVG figure export | `dataviz` |
| Changing `server.py` routes, the artifact route, or anything near credentials | `security-review` |
| Any commit (`safe-commit`); opening or merging a PR, with an independent review before merge | `safe-commit` / `sop-ship-pr` + `code-review` |
| A change moves a test count or makes this guide untrue | `sop-project-guide` |

## Before you finish any task

1. `python -m pytest -q` -> `517 passed, 6 skipped, 48 subtests passed` (more passed is fine if you added tests).
2. If you touched the UI: fixture-mode serve loads `/` and `/api/status` reports `"data_mode":"fixture"`.
3. No new network call outside `census_explorer/retrieve/`; no new third-party dependency.
4. `git diff --stat` -> only files you meant to change; nothing under `data/`, `artifacts/` or `site/`.

## Model routing

Safe for Sonnet: docs, UI copy and CSS, adding offline tests, running the offline commands above,
fixture-mode checks.

Escalate to Opus: measures, denominators, universes and uncertainty; `compare.py` eligibility;
geography joins and boundary vintages; `sentinels.py`; provenance, manifests and project pinning;
`site.py` output contract; hospital registry rules; anything that conflicts with `AGENTS.md`
or `docs/DATA_HANDLING.md`. Per `AGENTS.md`, state the files and why before editing more than two.
