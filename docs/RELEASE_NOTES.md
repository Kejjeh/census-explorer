# Release candidate: changes since the published source `1dc0965`

Status: **published on 2026-09-25** at
<https://kejjeh.github.io/census-explorer/>, after independent review. PR #1 is
not merged.

| Publication | |
| --- | --- |
| `gh-pages` commit | `eb96a10465d6c900f2827ebb10a5449f0e72e249` (a fast-forward from `c5f0c4f`) |
| Source revision | `efc9ea107c9b6f4c71694aadf8b64070613cc0f6` |
| Snapshot | `ddac376b20c21661b0b92e02bd82435de7851f3d8a5112b50e7b9bb66fb7f60c` |
| Pages deployment | "pages build and deployment" run 36118298955, success, 2026-09-25 09:24:57Z to 09:25:19Z |
| Rollback target | `c5f0c4f235b0407b4d0af14c9c40a8205bfc08d7`, the previous publication (built from `1dc0965`, snapshot `bb4b31d6…`). To roll back, add a commit restoring that tree; do not force-push. |

| | |
| --- | --- |
| Source revision (tested) | `efc9ea107c9b6f4c71694aadf8b64070613cc0f6` |
| Built from | the tree at that revision, unmodified |
| Release | 2019-2023 ACS five-year (`acs5_2023`), New York State, 62 counties and 5,411 listed census tracts |
| Artifact | `artifacts/release-candidate-efc9ea1/` in the implementer's environment (a git-ignored local copy; its `census-explorer/` is exactly what `gh-pages` `eb96a10` publishes). It holds `census-explorer/` (the site), `census-explorer-efc9ea1-site.tar.gz`, `FILES.sha256` and `acceptance-static-journeys.md` |

## What changed for readers

Changes since `1dc0965`, the source of the current publication:

- **Mobile and focus** (`a3d11f3`).
  - Focus now follows the reader when a control replaces itself: after choosing a search result, it moves to the chosen place; after Explore, Reset, "Try again" and the scope-switching comparison action, it moves to the scope line.
  - At 390 px the table scrolls with the page instead of inside a box three rows tall.
  - The rows-per-page select and the "Published table codes" toggle are at least 24 px tall.
  - Chart grid and zero lines no longer run through place names.
- **Printed briefs** (`35fc9b3`).
  - The local brief's chart no longer prints its axis labels over its subtitle.
  - The published brief no longer ends notes with a doubled full stop.
- **Honest coverage wording** (`35fc9b3`). A New York City tract brief says that 3 of its 2,327 tracts have no boundary shape, and states the build-wide figure (16 across the state) separately. Before, it quoted the state's 16, with a population of "0.0".
- **Shorter keyboard route** (`efc9ea1`).
  - From the table, a "Skip to result actions" link (shown on focus) reaches Share, Open brief and Download CSV / Export in 3–5 key presses. Before, it took 26–28 presses backward, or 10–19 forward, and forward only by wrapping past the end of the page.
  - "Back to the table" follows the last action. The page top adds "Skip to the table" and "Skip to result actions".
  - Skip links no longer rewrite the address, so a shared `#view=` link survives.
  - Disabled actions stay disabled.

## What changed for maintainers

- **Guarded acceptance harness** (`143fece`, `48cb3b2`). `tests/acceptance/journeys.mjs` and `briefs.mjs` drive a real browser against the live build: journeys, a 390 px layout audit, a keyboard-only pass, and briefs printed to PDF with every page saved as an image. Before anything is rendered or reported, both scripts read each target's own status and refuse anything but `data_mode: "live"`. Offline tests drive that gate against controlled responses. The recipe is in `docs/ACCEPTANCE.md`.
- **No change to** retrieval, measures, estimates, margins of error, reconciliation or boundary joins. The values in the candidate are byte-identical to the published values (see below).

## Reproduce the candidate

The candidate uses the existing verified cache. Nothing is fetched. From a checkout at `efc9ea1` with that cache:

```bash
python3 -m census_explorer.cli verify manifests                 # manifest verification: OK
python3 -m census_explorer.cli verify catalog --release acs5_2023   # 47 measures ... OK
python3 -m census_explorer.cli build --release acs5_2023        # offline; rewrites data/processed/acs5_2023
mkdir -p data/release-acs5_2023 && cp -a data/processed/acs5_2023 data/release-acs5_2023/
python3 -m census_explorer.cli site build --base /census-explorer/ \
  --data-dir data/release-acs5_2023 --out artifacts/release-candidate-efc9ea1/census-explorer
```

**Why a separate data directory.** The site is built from a data directory holding only the published release. The comparison panel adds a line whenever another release is present in the data directory. The implementer's data directory also holds a 2018-2022 build, and building from it added that line, with developer instructions, to four comparison panels. The publication was built from a directory with one release. Building from one here keeps the candidate on the same basis.

**Processed-data provenance.** The processed dataset was rebuilt from the cache at the clean revision `efc9ea1`, and it records that revision. The previous processed build recorded `78c1843…+dirty`. 52 of the 53 processed files are unchanged, including all 47 values files; only `dataset.json` changed, and only in `built_at` and `code_revision`. The raw data behind it is unchanged. Retrieval manifest: `observations-summary-file-acs5_2023-20260924T033904+0000`, plus the metadata, geography, roster and reconciliation manifests listed in `data/manifest.json`. `verify manifests` re-hashed every cached artifact and found no problem.

## Identity of this build

| Item | Value |
| --- | --- |
| Snapshot (in `index.html`; checked by share links) | `ddac376b20c21661b0b92e02bd82435de7851f3d8a5112b50e7b9bb66fb7f60c` |
| `data/digests.json` SHA-256 (the page's per-file digest map) | `803169adb78355c18979ac98afdd3502a222bfefbaa4918c3c9fedb8b7b4ab44` |
| `data/manifest.json` SHA-256 | `bda32459dba9c8d7f05f8b752c2c007704f2269a77ed7a4a7858ad4cfb6f07db` |
| Content fingerprint: SHA-256 of the sorted `path<TAB>sha256` lines of the 110 files other than the three above | `2513ab420a20c112a1ad5e97e69686c617cf48ded929e9503da2e77ee192f35e` |
| `census-explorer-efc9ea1-site.tar.gz` SHA-256 | `c9284e993646d9a685a20a5589dae24bd96e0c9e258682d321ff2b2e66e81129` |
| Size | 113 files, 16.0 MB (3.2 MB compressed) |

**Which values to expect on a rebuild.** Rebuilding from the same inputs reproduces the 110 other files byte for byte; this was checked with a second build. `data/manifest.json` records `generated_at`, so it changes on every build, and with it `data/digests.json` and the snapshot. The snapshot therefore identifies one build, not its content: compare the content fingerprint instead. Because the snapshot differs from the publication's, every share link made on the current publication is refused by the candidate, with the existing "different published snapshot" notice.

## Differences from the current publication

Compared file by file against `gh-pages` at `c5f0c4f`. Both have the same 113 paths. **103 files are byte-identical**, including every file under `data/values/`, `data/geography/`, `data/reference/`, `data/benchmarks/` and `data/quality/`, plus `data/catalog.json` and `data/areas.json`. The publication was built from the reviewer's own, separate retrieval, so this is also a cross-check of the two retrievals: identical published numbers.

The 10 files that differ:

| File | Why |
| --- | --- |
| `app.js`, `app.css`, `core.js`, `publish.js`, `static.js` | The interface changes listed above. |
| `index.html` | Skip links and the result-actions group, and the new snapshot. |
| `data/dataset.json` | `built_at`, `code_revision` (`1dc0965` → `efc9ea1`), and retrieval manifest ids (the reviewer's retrieval → the implementer's). |
| `data/status.json` | `code_revision`. |
| `data/manifest.json` | `generated_at`, `code_revision`, and retrieval manifest ids. |
| `data/digests.json` | Follows from the above. |

## Evidence, by who produced it

**Implementer, Linux, headless Chromium.** These are the implementer's own runs.
- At `efc9ea1`: `python -B -m unittest discover -s tests -t .` passed 478 tests, 3 skipped; `node --test tests/js/core.test.js` passed 42/42.
- On the candidate artifact itself, served under `/census-explorer/`: `journeys.mjs --static` passed 85 checks, 0 failed. Report: `acceptance-static-journeys.md`.
- On the local service and a static build of the same code at `efc9ea1`: all journeys passed, 0 failed. These include:
  - a measured 390 CSS px viewport;
  - a keyboard-only pass;
  - the result-actions route: 3 / 4 / 5 presses in static mode, 4 / 5 locally.
- Printed briefs, at `143fece` (the brief code has not changed since): 13 A4 PDF pages from four briefs, drawn by Chromium's PDF viewer and inspected by eye.

**Independent reviewer.** As reported by the reviewer; not reproduced or claimed by the implementer.
- `efc9ea1`, Windows: `python -B -m unittest discover -s tests -t .` ran 478 tests in 34.185 s, OK with 6 skipped, with the bundled Node.
- `efc9ea1`: the diff of `web/index.html`, `app.js` and `app.css` was reviewed, and no blocking issue was found.
- `efc9ea1`, real Chrome, local service, desktop viewport: Skip to the table → Tab → Enter → Tab → Tab lands on Open brief, 4 keystrokes from the table, with the URL unchanged. This was **not** an independent check at 390 px or of the static site.
- `8083c02`, Windows full suite: 466 tests, OK, 6 skipped. `48cb3b2`, Windows: the harness tests passed 7 of 7.
- The reviewer's independent statewide retrieval and build reconciled 6/6 for the state and for New York City, and the 111 digested assets of the current publication matched.

## After publication (implementer)

These checks were run after deployment, by the implementer:
- **Deployed bytes.** `data/digests.json` was downloaded from the public site; its SHA-256 is `803169ad…`, as expected. Every one of the 113 files in its inventory, including `.nojekyll`, was then downloaded with a cache-busting query. All returned HTTP 200, all 111 listed digests match the deployed bytes, and every file matches the candidate's `FILES.sha256`.
- **Public smoke test.** Headless Chromium at 390 CSS px, through the session proxy, trusting only the proxy CA's public key: 17 of 17 checks passed.
  - The live build is `efc9ea1`, snapshot `ddac376b…`.
  - 62 counties, 5,411 tracts, and Erie County's 261 tracts.
  - The keyboard route: Share, Open brief and Download CSV in 3, 4 and 5 presses from the table, with the address unchanged.
  - Margin-of-error and "no data" labels, with Suffolk tract 36103145601's reason.
  - A current-build share link restores the view and survives a reload.
  - A link made on the previous publication (snapshot `bb4b31d6…`) is refused with "It was made from a different published snapshot of the data".
  - No console errors, and no request outside `/census-explorer/`.

  The script and its output are kept with the candidate artifact.

## Limits that still apply

- **Chromium only.** No Firefox, Safari, physical printer or US Letter paper.
  - The 390 px and PDF evidence is the implementer's, from emulated viewports in headless Chromium.
  - The reviewer's real-Chrome check was desktop and local only.
- **No formal accessibility audit.** No screen-reader pass and no WCAG audit. Focus order and visibility were measured; announcements were not.
- **No cross-vintage comparison.** Comparing 2019-2023 with 2018-2022 stays blocked: boundary equivalence between the vintages has not been established. The 2018-2022 build is still New York City only.
- **Share margins above 100 points.** For tracts with tiny denominators, the documented formula yields share margins above 100 percentage points, which are printed as computed.
- **No user research.** Demand is a hypothesis.
