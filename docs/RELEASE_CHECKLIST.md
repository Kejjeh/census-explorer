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
6. Read `site/data/manifest.json`: release, citation, tables, build manifest
   id, counts, join reports, and what this copy cannot do.
7. Publish, then compare the deployed files against `site/data/digests.json`.

## What is true of the current implementation

| | State |
| --- | --- |
| Offline test suite | 461 Python tests, 3 skips on Linux (live network), also passing with every text-mode write forced to CRLF as an imitation of Windows; more skips on Windows. Node: 40 page-logic tests. Results on Windows itself are recorded by the independent reviewer, not here |
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

## Open items

- **PDF pagination is unverified.** The published brief sets `@page` size and
  margins and avoids breaking rows, but no one has looked at the paginated
  output. The browser tooling available in this environment blocks inspecting
  the generated blob tab, and that policy was not worked around. This is a
  gap in visual evidence only: the brief's content is covered by unit tests
  and by reading the rendered HTML.
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
  order, focus movement and small-screen layout were exercised in Chromium.
  No screen-reader pass and no formal WCAG audit has been done.
- **Per-shape keyboard focus on the map stops at 60 features.** At tract
  level, and for the 62 counties statewide, the table and the place search
  are the keyboard route, and the map's accessible name says so.
- **Real-network timings are unmeasured.** The statewide timings above are
  over loopback. On a slow connection the 4.3 MB tract boundary file will
  dominate the first tract view.
