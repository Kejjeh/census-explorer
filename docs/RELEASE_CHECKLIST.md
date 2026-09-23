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
   inspector, a two-place comparison, the reference, a tract view, a CSV
   download, a printable brief, a share link copied and reopened.
5. Confirm in the browser's network panel: no request outside the site
   prefix, no `/api/` request, no console error.
6. Read `site/data/manifest.json`: release, citation, tables, build manifest
   id, counts, join reports, and what this copy cannot do.
7. Publish, then compare the deployed files against `site/data/digests.json`.

## What is true of the current implementation

| | State |
| --- | --- |
| Offline test suite | 370 tests, 3 skips on Linux (live network); more on Windows |
| Data | Official ACS 2019-2023 five-year aggregates for the five boroughs and their census tracts, built from a recorded manifest |
| Published copy computes nothing | Every estimate, margin of error, CV, reliability wording, denominator and reference is computed by the Python during the build; a round-trip test compares the page's uncertainty panels and CSV against the service's own output |
| CSV | Byte-identical to the local exporter for the same selection, checked against the real dataset at borough and tract scope |
| Printable brief | Generated in the browser from the published snapshot; its table is capped at 25 rows in GEOID order and says so; the CSV keeps every selected row |
| Share links | Restore release, measure, level, scope, inspected place, comparison and reference; refuse a link from a different published snapshot; exclude zoom and table sort |
| Integrity | Every published data file is checked against a SHA-256 recorded in the page before it is used |
| Output directory | `--out` refuses the filesystem root, a home directory, the repository, a directory containing it, a git checkout, a file, and any non-empty directory that is not a previous build; a failed build leaves the previous copy intact |
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
  cannot be compared against it until boundary equivalence is established.
- **Accessibility has been checked, not audited.** Keyboard reach, focus
  order, focus movement and small-screen layout were exercised in Chromium.
  No screen-reader pass and no formal WCAG audit has been done.
- **Per-shape keyboard focus on the map stops at 60 features.** At tract
  level the table and the place search are the keyboard route, and the map's
  accessible name says so.
