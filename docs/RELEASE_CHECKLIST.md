# Release checklist

What has to be true before a build is published, what is true of the current
one, and what is still open. Written to be checked, not to reassure.

## Before publishing a build

1. `python -B -m unittest discover -s tests -t .` passes on the publishing
   machine. Record the count and the skips; skips are expected for the live
   network tests and, on Windows, for the symbolic-link cases.
2. `python -m census_explorer.cli verify manifests` passes.
3. `python -m census_explorer.cli site build --base /census-explorer/ --out site`
   completes and prints the file count and size.
4. Serve `site/` locally and walk the journey: a measure, a place, the
   inspector, a two-place comparison and its chart, the reference, a tract
   view, New York State, one upstate and one Long Island county's tracts, a
   page past the first, a CSV download, a printable brief, a share link
   copied and reopened, and an older link without a scope (it must open New
   York City, not the state).
5. Confirm in the browser's network panel: no request outside the site
   prefix, no `/api/` request, no console error.
5a. Run the browser acceptance recipe in `docs/ACCEPTANCE.md` against the
   local service and the static site: `tests/acceptance/journeys.mjs` must
   report 0 failing checks. Both scripts refuse any target whose own status
   metadata is not `data_mode: "live"`, before rendering or reporting
   anything. Then run `tests/acceptance/briefs.mjs` and **look
   at every page PNG it writes**; its checks are not visual acceptance.
6. Read `site/data/manifest.json`: release, citation, tables, build manifest
   id, counts, join reports, and what this copy cannot do.
7. Publish, then compare the deployed files against `site/data/digests.json`.

## What is true of the current implementation

| | State |
| --- | --- |
| Offline test suite | 478 Python tests, 3 skips on Linux (live network); the CRLF imitation of Windows was last run at 466 tests (8083c02); more skips on Windows. Node: 42 page-logic tests. Results on Windows itself are recorded by the independent reviewer, not here |
| Data | Official ACS 2019-2023 five-year aggregates for every county and census tract in New York State, built from a recorded manifest; New York City is its documented five-borough subset and the default view |
| Retrieval sources | Table-based Summary File tables B01003, B05002, B05006, B06004B, B06009 (`acsdt5y2023-*.dat`), the release's geography file `Geos20235YR.txt`, and GENZ2023 cartographic boundaries `cb_2023_36_tract_500k.zip` and `cb_2023_us_county_500k.zip`. Keyless; every artifact has a manifest record with the digest of the complete upstream file |
| Coverage | Release roster: 1 state, 62 counties, 5,411 tracts. Table rows: 1, 62, 5,396. Boundaries: 62 counties, 5,395 tracts. 15 listed tracts have no table row (14 in Suffolk, 1 in Ulster) and are shown as unavailable; 16 water tracts (population 0) have rows but no polygon and are listed by GEOID. 0 boundary features without observations. Each tract's county agrees with the boundary file's STATEFP and COUNTYFP (5,395 of 5,395) |
| Reconciliation | 62 counties add up exactly to the published state row, 6/6 cells. The five boroughs still add up exactly to the published New York City row, 6/6 cells. All 109,604 New York City value records are identical to the city-only build |
| Scope | One scope drives the map, table, search listing, CSV, brief and share link. A request, link or saved view with no scope is New York City; version 1 share links replay as the city and cannot name a place outside it |
| Statewide size and speed (Chromium, loopback, this machine) | Site 16.0 MB in 113 files; tract boundaries 4.3 MB, fetched once. First view 0.26 s; all 2,327 city tracts 0.29 s; all 5,411 state tracts 0.34 s (5,395 shapes); measure change there 0.44 s; JS heap about 22 MB. Loopback timings exclude network transfer |
| Published copy computes nothing | Every estimate, margin of error, CV, reliability wording, denominator and reference is computed by the Python during the build; a round-trip test compares the page's uncertainty panels and CSV against the service's own output |
| CSV | Byte-identical to the local exporter for the same selection, checked against the real dataset at borough and tract scope; an Erie County tract view downloads exactly its 261 tracts |
| Printable brief | Generated in the browser from the published snapshot; its table is capped at 25 rows in GEOID order and says so; the CSV keeps every selected row |
| Share links | Version 2 restores release, measure, level, scope (city, state or one county), inspected place, comparison and reference; refuse a link from a different published snapshot with the existing notice (the statewide build is a new snapshot, so every earlier link is refused rather than replayed); exclude zoom and table sort |
| Integrity | Every published data file is checked against a SHA-256 recorded in the page before it is used |
| Output directory | `--out` refuses the filesystem root, a home directory, the repository, a directory containing it, a git checkout, a file, any non-empty directory that is not a previous build of this site, and any previous build that holds a file this build did not write. Staging is a uniquely created directory, so one build cannot delete another's work. The previous build is kept until the new one is in place, and put back if the last step fails |
| Starting examples | Three, resolved against the build, each naming its measure, denominator, places, period and what it does not say |
| Saved views and export bundles | Local service only, and the published copy says so rather than approximating them |
| Comparing two reference periods | Blocked at every level; boundary equivalence between vintages is not established |

## Browser and print evidence (release-readiness pass)

Run on 2026-09-24 at commit `143fece`, against the live 2019-2023 build.
Targets: the local service (`http://127.0.0.1:8765/`) and the static site
served under `/census-explorer/`. Tool: headless Chromium through the
environment's existing Playwright. This is the implementer's own run; the
independent Windows test run and the published-asset digest comparison
belong to the reviewer and are not claimed here.

| Evidence | Result |
| --- | --- |
| `journeys.mjs`, both targets | 125 checks passed, 0 failed (31 s). Covers county to tract then share and compare; a comparison kept across a scope change; share links on reload, including older and stale links (static); and a genuine load failure with recovery. |
| Narrow layout | Measured `innerWidth` 390, `clientWidth` 390, `devicePixelRatio` 1. No horizontal overflow, and no control past the viewport or with a cut-off label, on the first view, on New York State counties, or on Erie tracts with a comparison. The only control under 24 px is the inline "Which, and why" toggle inside a sentence of the static notice. |
| Keyboard only at 390 px | Done with Tab, Shift+Tab, Enter, Space, arrows and typing, with visible focus at every stop: New York State → search Erie County → Explore its tracts → a measure → the table (one tab stop) → compare two tracts → Share (static) → Open brief → Download CSV or Export. The longest single reach in that run was 47 Tab presses backwards, from the table to Download CSV. |
| Table to result actions (before and after) | Erie County tracts at 390 px, static site; the local counts are the same or one fewer. **Before** (`48cb3b2`), plain Tab order from the table's row stop, fewest presses to Share / Open brief / Download CSV: <br>• nothing inspected: 10 / 11 / 12 forward, but only by wrapping past the end of the page, which in a desktop browser also passes through the browser toolbar; 28 / 27 / 26 backward; <br>• one place inspected and two compared: 17 / 18 / 19 forward, again by wrapping; 28 / 27 / 26 backward. <br>An earlier keyboard journey, in a different state, needed 31–47 presses. **After**, by the skip route: 3 / 4 / 5 presses (locally 4 / 5 to Open brief / Export). The route is Tab to "Skip to result actions" (shown on focus), Enter to the labelled "Result actions" group, then Tab. "Back to the table" follows the last action. The page top has "Skip to the map", "Skip to the table" and "Skip to result actions". Skip links move focus without changing the page address, so a shared `#view=` survives. Disabled actions stay disabled and are passed over while a view loads or after a failure. There is no positive tabindex and no new keyboard shortcut. The natural Tab order is unchanged apart from the added links. |
| Defects found and fixed in this pass | Focus fell to the page after choosing a search result, and after any control that replaces itself (Explore, Reset, the scope-switching comparison action, Try again). The 390 px table scrolled inside a three-row box. Two targets were under 24 px. Chart grid lines ran through place names. The starter text said "list on the left". Commit `a3d11f3`. |
| Printed briefs | `briefs.mjs` printed four briefs to A4 PDF: from each target, the Bronx naturalised share with the New York City reference, and the 25-row New York City tract table for the Dominican-Republic share. Local: 3 and 5 pages; published: 2 and 3 pages. Every one of the 13 pages was opened in Chromium's PDF viewer, saved as an image, and inspected by eye. |
| What the pages show | No clipped columns. No row split across a page; table headers repeat on continuation pages. Headings, the denominator, margins of error, no-data reasons, sources and reproducibility details are all visible. |
| Print defects found and fixed | The local chart's tick labels overlapped its subtitle. The published brief printed doubled full stops. A New York City tract brief quoted the state's 16 boundary-less tracts instead of its own 3, with a population of "0.0". Commit `35fc9b3`. |

## Release candidate (`efc9ea1`)

Tested source head: `efc9ea107c9b6f4c71694aadf8b64070613cc0f6`. The
candidate is described in `docs/RELEASE_NOTES.md`:
- the build command and data provenance;
- the snapshot `ddac376b…`, the `data/digests.json` SHA-256 `803169ad…` and the
  content fingerprint `2513ab42…`;
- the file-by-file differences from the current publication (`gh-pages`
  `c5f0c4f`, built from `1dc0965`). 103 of 113 files are byte-identical,
  including every published value.

| Evidence at `efc9ea1` | Who | Result |
| --- | --- | --- |
| Full offline suite, Linux | implementer | 478 tests, OK, 3 skipped; Node 42/42 |
| Full offline suite, Windows, bundled Node | independent reviewer | 478 tests in 34.185 s, OK, 6 skipped |
| Diff review of `web/index.html`, `app.js`, `app.css` | independent reviewer | no blocking issue |
| Real Chrome, local service, desktop | independent reviewer | Skip to the table → Tab → Enter → Tab → Tab lands on Open brief (4 keystrokes from the table); URL unchanged. Not an independent check at 390 px or of the static site |
| `journeys.mjs`, local service and static build, headless Chromium, including 390 px and keyboard only | implementer | all journeys, 0 failed |
| `journeys.mjs --static` on the candidate artifact itself | implementer | 85 checks, 0 failed |
| Printed briefs, 13 pages inspected by eye (brief code unchanged since `143fece`) | implementer | see the print row above |

## Publication record

| | |
| --- | --- |
| Published | 2026-09-25, `gh-pages` `eb96a10465d6c900f2827ebb10a5449f0e72e249`, a fast-forward from `c5f0c4f` |
| Source | `efc9ea107c9b6f4c71694aadf8b64070613cc0f6` (the release notes are on the source branch at `bfd8284` and later) |
| Snapshot | `ddac376b20c21661b0b92e02bd82435de7851f3d8a5112b50e7b9bb66fb7f60c` |
| Rollback target | `c5f0c4f235b0407b4d0af14c9c40a8205bfc08d7` (the previous publication, built from `1dc0965`) |
| Deployment | Pages run 36118298955: success |
| Deployed bytes (implementer) | All 113 files downloaded and matched to the candidate; `data/digests.json` `803169ad…` |
| Public smoke (implementer) | 17/17 at 390 CSS px, headless Chromium; details in `docs/RELEASE_NOTES.md` |
| Independent checks of the deployment | As reported by the reviewer, not reproduced by the implementer: `origin/gh-pages` at `eb96a10`, deployed `data/digests.json` SHA-256 `803169adb78355c18979ac98afdd3502a222bfefbaa4918c3c9fedb8b7b4ab44`, all 111 listed assets downloaded and matched by SHA-256 (0 mismatches). |
| Certificate handling of the implementer's public smoke | The implementer's public smoke test ran Chromium through the session proxy with `--ignore-certificate-errors-spki-list` pinned to the proxy CA. The reviewer asked that no certificate-validation exception be used again: later checks use trusted access only, or report the TLS or browser blocker. |

## Open items

- **Page images are checked by eye, not by the script.** `briefs.mjs` checks
  that the page images are pairwise distinct files. That proves only that the
  bytes differ, not that each image is the page it is named for or that the
  page was completely drawn. Opening each page image stays a mandatory,
  manual step.
- **The acceptance gate was once open.** Up to `8083c02`, `briefs.mjs
  --local` did not check the local target's data mode. Against a fixture
  service it exited 0, writing a report titled "live data" (reproduced
  with a loopback stub). Both scripts now read each target's status metadata
  first and fail closed. The evidence recorded above was produced against
  services that report `data_mode: "live"`.
- **Print evidence is Chromium only.** The pages above were printed by
  Chromium and drawn by Chromium's own PDF viewer. Firefox, Safari and
  physical printers may paginate differently, and US Letter paper was not
  checked. The blob tab itself was still never opened by a script; the
  published brief's HTML was read back from the blob the page creates.
- **No user research.** The three starting examples are a design decision
  about first use, not a validated one. Nothing about customer demand or
  willingness to pay has been tested.
- **One release.** Only `acs5_2023` is published. `acs5_2022` is built but
  cannot be compared against it until boundary equivalence is established,
  and it is still the New York City-only build: statewide coverage was
  retrieved for 2019-2023 only.
- **State reference uncertainty.** The New York State reference is the
  state's own row; for a share its margin of error is the one this build
  derives for every share by the documented proportion formula. No
  aggregation variance is computed for the state.
- **Accessibility has been checked, not audited.** Keyboard reach, focus
  visibility and focus movement were exercised, and the 390 px layout was
  measured, in headless Chromium with emulated viewports. There has been no
  screen-reader pass, no formal WCAG audit, no physical phone and no touch
  gestures. The route from the table to Share, brief and export is now at most 5 key
  presses by skip link. The skip links were measured in headless Chromium only,
  not with a screen reader or a desktop browser's own tab cycle.
- **A share margin can exceed 100 points.** For tracts with very small
  denominators, the documented derived-margin formula yields margins such as
  ±1300.0 percentage points, and the brief prints them as computed. They are
  marked "wide — read as indicative". This was observed, not changed: no
  computation defect was reproduced.
- **Per-shape keyboard focus on the map stops at 60 features.** At tract
  level, and for the 62 counties statewide, the table and the place search
  are the keyboard route, and the map's accessible name says so.
- **Real-network timings are unmeasured.** The statewide timings above are
  over loopback. On a slow connection the 4.3 MB tract boundary file will
  dominate the first tract view.
