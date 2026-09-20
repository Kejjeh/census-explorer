# Runbook: from a fresh checkout to a brief

Every command here was run end to end against a fresh `git clone` of this
repository. The timings are the wall-clock times measured on that run, on the
machine this was built on and over its own network connection. They are
evidence that the steps work, not a benchmark: step 1 in particular depends
entirely on your connection to the Census Bureau.

Measured on the fresh checkout: 8.6 s for the test suite (210 tests at
the time of that run; 280 now), 10.3 s to retrieve
one release (435 MB streamed), 3.6 s to reconcile, 7.9 s to build, 0.3 s to
verify, and 0.7 s from opening the browser to the first drawn answer. The
resulting cache is 18 MB of raw data and 12 MB of built dataset.

No credential is needed. The Census Bureau publishes the same ACS estimates in
its table-based Summary File, which is open, and its metadata endpoints need no
key either.

PowerShell and POSIX shells take identical arguments. Only the interpreter name
(`python` vs `python3`) differs.

---

## 0. Check the checkout (8.6 s measured)

```powershell
git clone <this repository> census-explorer
cd census-explorer
python --version          # 3.11 or newer
python -m unittest discover -s tests -t .
```

Expected:

```
Ran 304 tests in 9.1s
OK (skipped=3)
```

The three skips are the live network tests, which are opt-in. **No third-party
package is required and none should be installed.**

Nothing has been downloaded yet. If you want to see the application before
retrieving anything, skip to step 6.

---

## 1. Retrieve the official bulk data (10.3 s measured, network-dependent)

```powershell
python -m census_explorer.cli fetch all --release acs5_2023
```

```
Retrieving all for 2019-2023 ACS (acs5_2023)
  metadata cached: B01003 (2019-2023 ACS)
  metadata cached: B05002 (2019-2023 ACS)
  metadata cached: B05006 (2019-2023 ACS)
  metadata cached: B06004B (2019-2023 ACS)
  metadata cached: B06009 (2019-2023 ACS)
  boundaries cached: county @ GENZ2023
  boundaries cached: tract @ GENZ2023
  streaming Summary File for B01003 ...
   B01003: kept 2332 rows from 18.3 MB upstream
  streaming Summary File for B05002 ...
   B05002: kept 2332 rows from 57.6 MB upstream
  streaming Summary File for B05006 ...
   B05006: kept 2332 rows from 278.5 MB upstream
  streaming Summary File for B06004B ...
   B06004B: kept 2332 rows from 14.6 MB upstream
  streaming Summary File for B06009 ...
   B06009: kept 2332 rows from 65.7 MB upstream
done. Next: python -m census_explorer.cli build --release acs5_2023
```

2,332 rows is five boroughs plus 2,327 census tracts. About 435 MB is streamed
and about 3.4 MB retained, along with the SHA-256 and byte length of each
complete upstream file so the download can be re-verified.

**What to check.** Every table reports the same row count. If one differs, the
geography filter and the published file disagree and the build will say so.

---

## 2. Reconcile against an independently published row (3.6 s measured)

New York City as a place is exactly the five boroughs, and the Census Bureau
publishes it as its own row in the same tables. Summing the borough rows must
reproduce it.

```powershell
python -m census_explorer.cli reconcile --fetch --release acs5_2023
```

```
"checked": 6, "matched": 6, "passed": true
  B01003_001  total population            8,516,202 = 8,516,202   difference 0
  B05002_001  total population            8,516,202 = 8,516,202   difference 0
  B05002_013  foreign-born                3,108,052 = 3,108,052   difference 0
  B05002_003  born in state of residence  4,112,444 = 4,112,444   difference 0
  B06004B_001 Black alone                 1,933,195 = 1,933,195   difference 0
  B06009_001  population 25 and over      6,090,653 = 6,090,653   difference 0
```

**What to check.** `passed: true` and six differences of zero. The command
exits non-zero on any mismatch, and a mismatch is reported with its size rather
than absorbed. This is the check that catches a wrong cell, a wrong geography
filter or a parsing error, so run it before trusting anything downstream.

---

## 3. Build the analysis dataset (7.9 s measured, offline)

```powershell
python -m census_explorer.cli build --release acs5_2023
```

```
Building 2019-2023 ACS (acs5_2023) from the cache
  join: {"level": "county", "matched": 5, "unmatched_feature_count": 0, "unmatched_observation_count": 0}
  join: {"level": "tract", "matched": 2324, "unmatched_feature_count": 0, "unmatched_observation_count": 3}
  warning: tract @ GENZ2023: 2324 matched, 0 boundary features without observations, 3 observations without a boundary
  dataset written: data/processed/acs5_2023/dataset.json
```

**What to check.** The three unmatched tracts are `36047990100`, `36081990100`
and `36085990100`: water tracts with a published population of zero, which the
cartographic boundary files exclude. They are reported, not dropped. Any other
unmatched identifier deserves investigation before you publish anything.

---

## 4. Verify the cache and the catalog (0.3 s measured, offline)

```powershell
python -m census_explorer.cli verify manifests
python -m census_explorer.cli verify catalog --release acs5_2023
```

```
  ... 8 manifests, 32 artifacts ...
manifest verification: OK
47 measures checked against 2019-2023 ACS: OK
```

`verify manifests` re-hashes every cached file against its manifest.
`verify catalog` re-checks every measure's cells, universes and margins of
error against the published release metadata.

---

## 5. Run the application

```powershell
python -m census_explorer.cli serve
```

```
Census Explorer service on http://127.0.0.1:8765/
  data mode: live   releases: acs5_2023
  network access is disabled in this process; press Ctrl+C to stop
```

Open <http://127.0.0.1:8765/>. The first screen already shows an answer — a
question, a place and a measure are chosen and drawn, in 0.7 s on the measured
run. Switching to the tract-level question redraws 2,324 tract polygons in
about 3.6 s more.

1. **Pick a question.** Three are offered and each says what it answers and
   what it does not.
2. **Pick the place.** One borough, several boroughs, or every census tract.
3. **Pick what to show,** and optionally a reference to read it against.
4. **How far to trust this** opens three separate statements: uncertainty,
   comparison eligibility and reference period.
5. **Open the brief** produces a print-ready page. Use the browser's own print
   dialog to save it as PDF.
6. **Export brief + data** writes `brief.html`, `data.csv`, `provenance.json`
   and `figure.svg` to `artifacts/`.
7. **Save** pins the digests of every file the brief was built from. Reopening
   verifies them and refuses, naming the file, if any has changed.

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

## 7. Optional: the comparison period

```powershell
python -m census_explorer.cli fetch all --release acs5_2022
python -m census_explorer.cli reconcile --fetch --release acs5_2022
python -m census_explorer.cli build --release acs5_2022
```

Comparing the two periods is **refused**, and will stay refused until someone
records documented provider correspondence or a scoped review in
`config/geography_equivalence.json`. To see how far the two vintages' published
footprints actually differ:

```powershell
python -m census_explorer.cli geography footprint --a acs5_2023 --b acs5_2022 --level tract
```

```
Footprint comparison of 2324 shared tract area(s) between GENZ2023 and GENZ2022:
2251 agree within the measurement tolerance and 73 do not. Worst observed:
relative area 0.073280, boundary distance 78.4 m, centroid shift 19.0 m.
This is a measurement, not an establishment of equivalence.
computed in 0.5s
```

At county level, 2 of 5 agree and 3 do not. See `DATA_HANDLING.md` for why a
measurement cannot establish equivalence on its own.

Comparing **places within one period** is unaffected and needs none of this.

---

## Printing a brief

"Open the brief" produces a self-contained HTML page; the browser's own print
dialog turns it into a PDF. Measured on A4 with 16 mm margins (a 674 x 1003 px
content box at 96 dpi):

| Brief | Laid-out height | Pages | Unsplittable elements taller than a page | Horizontal overflow |
| --- | --- | --- | --- | --- |
| One borough, naturalisation share with the NYC reference | 2,796 px | 3 | none | 0 px |
| 25-row tract table, Dominican-Republic share | 4,872 px | 6 | none | 0 px |

The tract brief takes six pages where its height alone implies five: the
stylesheet keeps table rows, the figure and the quality panels from splitting,
so the browser moves them rather than cutting them. The table itself is allowed
to flow across pages; its tallest single row is 81 px against a 1,003 px page
box, so no row can be split.

Page counts were read from the generated PDF's own page tree. The pages were
not rasterised and inspected visually — there is no PDF renderer in this build
and adding one would be a new dependency.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `no built dataset found` on `serve` | Run `build` first, or `serve --data-dir data/fixture-processed`. |
| `CENSUS_API_KEY is not set` | Only the `--transport api` path needs a key. The default Summary File path does not. |
| `metadata for B05002 is not cached` | Run `fetch metadata` for that release. |
| `manifest verification: FAILED` | A cached file changed or went missing. The message names it. Re-fetch that release. |
| A saved brief refuses to reopen | Its inputs changed since it was saved. The message names the file. Restore it, or re-save the brief deliberately. |
| `this comparison is blocked` | Expected across reference periods. See step 7. |
| Port 8765 already in use | `serve --port 8866`. |

## What this runbook does not cover

Historical data, IPUMS or NHGIS, any provider other than the Census Bureau,
deployment, and multi-user access. None of those exist in this build.
