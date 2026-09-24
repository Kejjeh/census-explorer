# Runbook: from a fresh checkout to a brief

The project is configured for **New York State**: every county and census
tract, from the 2019-2023 ACS five-year release, with New York City as the
documented five-borough subset and the default view. This runbook follows
that configuration.

**What the evidence here is.** Each transcript below was produced on
2026-09-24 in the environment this was built in, from its own cache. Where a
step's current output was not captured end to end, the runbook says so rather
than presenting an older run as current. Older New York City-only
measurements are kept at the end, under
[Historical evidence](#historical-evidence-new-york-city-only-build), and are
labelled as such. Timings are wall-clock times on that machine, not a
benchmark; retrieval in particular depends on your connection.

No credential is needed. The Census Bureau publishes the same ACS estimates in
its table-based Summary File, which is open, and its metadata endpoints need no
key either.

PowerShell and POSIX shells take identical arguments. Only the interpreter name
(`python` vs `python3`) differs.

---

## 0. Check the checkout

```powershell
git clone <this repository> census-explorer
cd census-explorer
python --version          # 3.11 or newer
python -B -m unittest discover -s tests -t .
```

Expected on Linux (18.8 s measured):

```
Ran 461 tests in 18.5s
OK (skipped=3)
```

The three skips are the live network tests, which are opt-in. Windows skips
more (the symbolic-link cases). Tests that exercise the browser code run under
Node when it is installed and skip with a message when it is not; Node is not
needed for the service, the data pipeline or any other test. **No third-party
package is required and none should be installed.**

Line endings do not matter: cached files are read the same whether their lines
end in LF or CRLF, and `tests/test_roster.py` checks both byte for byte on
every platform. The Windows result for this commit is recorded by the
independent reviewer, not here.

Nothing has been downloaded yet. To see the application before retrieving
anything, skip to step 6.

---

## 1. Retrieve the official bulk data (network-dependent)

```powershell
python -m census_explorer.cli fetch all --release acs5_2023
```

`fetch all` retrieves, in order: table metadata, the GENZ2023 boundary files,
the five Summary File tables, and the release's own geography roster.
Captured output of the observations and roster steps (run separately on
2026-09-24; the combined `fetch all` was not timed end to end):

```
Retrieving observations for 2019-2023 ACS (acs5_2023)
  statewide coverage: state 36, levels state, county, tract
  streaming Summary File for B01003 ...
   B01003: kept 5459 rows from 18.3 MB upstream
  streaming Summary File for B05002 ...
   B05002: kept 5459 rows from 57.6 MB upstream
  streaming Summary File for B05006 ...
   B05006: kept 5459 rows from 278.5 MB upstream
  streaming Summary File for B06004B ...
   B06004B: kept 5459 rows from 14.6 MB upstream
  streaming Summary File for B06009 ...
   B06009: kept 5459 rows from 65.7 MB upstream
```

```
  streaming the 2019-2023 ACS geography file for state 36 ...
   geography roster: kept 5474 rows from 91.9 MB upstream
```

5,459 rows per table is the state row, 62 counties and 5,396 census tracts.
The roster, `Geos20235YR.txt` from the release's documentation directory,
lists 1 state, 62 counties and 5,411 tracts: 5,474 rows. About 435 MB of
tables and 92 MB of roster are streamed. Only New York's rows are kept,
together with the SHA-256 and byte length of each complete upstream file so
the download can be re-verified.

The statewide cache is written to its own files (`<table>_state36.psv`,
`Geos20235YR_state36.psv`). An earlier New York City-only cache
(`<table>.psv`) is left as it was, and so is its manifest.

**What to check.** Every table reports the same row count. The build (step 3)
then proves coverage against the roster rather than assuming it.

---

## 2. Reconcile against independently published rows (0.8 s each, offline)

Two checks, each of which must pass before anything downstream is trusted.

**New York City.** The city as a place is exactly the five boroughs, and the
Census Bureau publishes it as its own row. `--fetch` retrieves that row the
first time. After that, the check runs from the cache:

```powershell
python -m census_explorer.cli reconcile --fetch --release acs5_2023
python -m census_explorer.cli reconcile --release acs5_2023
```

```
  B01003_001  total population            8,516,202 = 8,516,202   difference 0
  B05002_001  total population            8,516,202 = 8,516,202   difference 0
  B05002_013  foreign-born                3,108,052 = 3,108,052   difference 0
  B05002_003  born in state of residence  4,112,444 = 4,112,444   difference 0
  B06004B_001 Black alone                 1,933,195 = 1,933,195   difference 0
  B06009_001  population 25 and over      6,090,653 = 6,090,653   difference 0
"passed": true
written to data/processed/acs5_2023/reconciliation.json
```

**New York State.** Every county row, added up, must equal the published state
row. The county list is checked against the release's roster first, so a
short list cannot pass by summing fewer counties:

```powershell
python -m census_explorer.cli reconcile --state --release acs5_2023
```

```
  B01003_001   62 counties  19,872,319 = 19,872,319   difference 0
  B05002_001   62 counties  19,872,319 = 19,872,319   difference 0
  B05002_013   62 counties   4,499,147 =  4,499,147   difference 0
  B05002_003   62 counties  12,453,224 = 12,453,224   difference 0
  B06004B_001  62 counties   2,927,008 =  2,927,008   difference 0
  B06009_001   62 counties  13,996,138 = 13,996,138   difference 0
"passed": true   (each cell: "county_roster_matches_release": true)
written to data/processed/acs5_2023/reconciliation_state.json
```

(Both outputs are JSON. The lines above condense the per-cell fields.)

**What to check.** `passed: true` and six differences of zero in each. Either
command exits non-zero on any mismatch, and a mismatch is reported with its
size rather than absorbed.

---

## 3. Build the analysis dataset (19.2 s measured, offline)

```powershell
python -m census_explorer.cli build --release acs5_2023
```

```
Building 2019-2023 ACS (acs5_2023) from the cache
  join: {"level": "county", "matched": 62, "unmatched_feature_count": 0, "unmatched_observation_count": 0}
  join: {"level": "tract", "matched": 5395, "unmatched_feature_count": 0, "unmatched_observation_count": 16}
  warning: tract @ GENZ2023: 5395 matched, 0 boundary features without observations, 16 observations without a boundary
  dataset written: data/processed/acs5_2023/dataset.json
```

**What to check.**

- The 16 tract observations without a boundary are water tracts, each with a
  published population of zero: 36011990200, 36013990000, 36029990000,
  36047990100, 36055990000, 36059990100, 36059990200, 36059990301,
  36059990302, 36059990400, 36063990000, 36073990000, 36075990000,
  36081990100, 36085990100 and 36103990100. They are reported, not dropped.
- 15 tracts the roster lists have no row in any table used here: 14 in
  Suffolk County (for example 36103145601) and 36111954401 in Ulster County.
  They are carried with the reason "listed by the release, no table row" and
  appear as "no data", never zero.
- Each tract's county, read from its GEOID, is checked against the boundary
  file's own STATEFP and COUNTYFP: 5,395 of 5,395 agree. A disagreement stops
  the build.
- A table row the roster does not list, or a damaged roster, stops the build
  and names the file.

Any other unmatched identifier deserves investigation before you publish
anything. The built dataset is about 30 MB.

---

## 4. Verify the cache and the catalog (offline)

```powershell
python -m census_explorer.cli verify manifests
python -m census_explorer.cli verify catalog --release acs5_2023
```

```
  ... 11 manifests, 43 artifacts ...
manifest verification: OK
47 measures checked against 2019-2023 ACS: OK
```

`verify manifests` (3.1 s) re-hashes every cached file against its manifest.
Those manifests include the earlier New York City-only and 2018-2022
retrievals, which is why there are 11. `verify catalog` (0.2 s) re-checks every
measure's cells, universes and margins of error against the published release
metadata. Reading the cache never rewrites it, so these digests are unaffected
by the platform's line endings.

---

## 5. Run the application

```powershell
python -m census_explorer.cli serve
```

Open <http://127.0.0.1:8765/>. The first view is New York City's five
boroughs. Measured in Chromium against the local service on 2026-09-24 over
loopback, which excludes network time: first map 1.2 s, and every New York
State tract (5,395 shapes) 1.5 s after switching.

1. **Choose the area.** New York City or New York State. At census-tract
   level, a picker lists each county (each borough, within the city) with its
   tract count. The line above the map always says what the map, table, CSV,
   brief and share link cover, and offers Reset to New York City.
2. **Choose a measure** from the sidebar, grouped by published concept.
3. **Find a place** by name or GEOID anywhere in the build. A place outside
   the current view is labelled so. At county level, a county's card offers
   "Explore this county's census tracts". Cities are not geographies here.
4. **Read every row.** The table pages through all of them (25, 50 or 100 at a
   time), with one tab stop and arrow-key and Page Up/Down movement.
5. **Compare two places.** The chart puts both on one axis that includes zero,
   with their published 90% margins of error. "Show only these two" narrows
   the view when both are in it. When they are not, the button names the view
   it switches to.
6. **Add a reference**: New York City (exactly the five boroughs), New York
   State (the state's own published row), or the containing county.
7. **Open the brief**, **export**, **save**. Export and saved views cover
   exactly the scope on screen; saving pins the digest of every input.

---

## 6. Without any data: fixture mode

```powershell
python -m census_explorer.cli fixtures build
python -m census_explorer.cli serve --data-dir data/fixture-processed
```

Every screen, figure and export is banner-marked `FIXTURE MODE`, the shapes are
labelled as generated rectangles rather than boundaries, and two of the five
areas deliberately exercise the unavailable paths. **Nothing produced in
fixture mode is a census finding.**

---

## 7. The comparison period (2018-2022): current state

Statewide coverage was retrieved for 2019-2023 only. The 2018-2022 dataset in
the reference environment is the **earlier New York City-only build**, and it
remains usable for city views. Running `fetch all --release acs5_2022` under
the current configuration would retrieve 2018-2022 statewide. That has **not**
been run or validated, so no step here depends on it.

Comparing the two periods is **refused** either way, and will stay refused
until someone records documented provider correspondence or a scoped review
in `config/geography_equivalence.json`. The footprint measurement that shows
why is under historical evidence below. Comparing **places within one
period** is unaffected.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `no built dataset found` on `serve` | Run `build` first, or `serve --data-dir data/fixture-processed`. |
| `statewide coverage needs the release's geography roster` | Run `fetch roster` (or `fetch all`) for that release. |
| `the release's geography roster could not be read: ...` | The cached roster is damaged: a missing column, a row with the wrong number of fields, or a GEO_ID listed twice. The message names the file and line. Re-run `fetch roster`. Line endings (LF or CRLF) are not a cause. |
| `CENSUS_API_KEY is not set` | Only the `--transport api` path needs a key. The default Summary File path does not. |
| `metadata for B05002 is not cached` | Run `fetch metadata` for that release. |
| `manifest verification: FAILED` | A cached file changed or went missing. The message names it. Re-fetch that release. |
| A saved brief refuses to reopen | Its inputs changed since it was saved. The message names the file. Restore it, or re-save the brief deliberately. |
| A share link says it was made from a different published snapshot | Expected for every link made before the statewide build. Open the current site instead. |
| `this comparison is blocked` | Expected across reference periods. See step 7. |
| Port 8765 already in use | `serve --port 8866`. |

---

## Historical evidence: New York City-only build

Kept for the record. **None of this describes the current statewide
configuration.**

**Fresh-clone timings (NYC-only, 210 tests at the time).** 8.6 s for the test
suite, 10.3 s to retrieve one release (435 MB streamed, 2,332 rows kept per
table: five boroughs plus 2,327 tracts), 3.6 s to reconcile, 7.9 s to build
(5 counties and 2,324 tracts matched; the 3 unmatched tracts were the city's
water tracts 36047990100, 36081990100 and 36085990100), 0.3 s to verify, and
0.7 s to the first drawn answer. The cache was 18 MB raw and 12 MB built.

**Boundary footprints (NYC-only builds of both periods).**

```
Footprint comparison of 2324 shared tract area(s) between GENZ2023 and GENZ2022:
2251 agree within the measurement tolerance and 73 do not. Worst observed:
relative area 0.073280, boundary distance 78.4 m, centroid shift 19.0 m.
This is a measurement, not an establishment of equivalence.
```

At county level, 2 of 5 agree and 3 do not. See `DATA_HANDLING.md` for why a
measurement cannot establish equivalence on its own.

**Printing a brief (NYC briefs).** Measured on A4 with 16 mm margins (a
674 x 1003 px content box at 96 dpi):

| Brief | Laid-out height | Pages | Unsplittable elements taller than a page | Horizontal overflow |
| --- | --- | --- | --- | --- |
| One borough, naturalisation share with the NYC reference | 2,796 px | 3 | none | 0 px |
| 25-row tract table, Dominican-Republic share | 4,872 px | 6 | none | 0 px |

Page counts were read from the generated PDF's own page tree. The pages were
not rasterised or inspected visually, so **PDF pagination remains
unverified**; no statewide brief has been measured.

---

## What this runbook does not cover

Historical data, IPUMS or NHGIS, any provider other than the Census Bureau,
deployment, and multi-user access. None of those exist in this build.
