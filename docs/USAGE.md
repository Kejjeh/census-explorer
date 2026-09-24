# Usage and verification

Every command below was run against this checkout. Output is quoted as it
appeared, abbreviated only where noted.

PowerShell and POSIX shells take identical arguments; only the interpreter name
(`python` vs `python3`) and the way you set an environment variable differ.

## 1. Retrieve (the only commands that use the network)

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

2,332 rows is five boroughs plus 2,327 census tracts: that was the New York
City-only retrieval, and its cache and manifest are kept. With the statewide
study area in `config/project.json` (`explorer.study_area`, coverage `state`),
the same command keeps New York State's rows instead, in a separate file
(`<table>_state36.psv`), and also retrieves the release's own geography roster:

```text
  statewide coverage: state 36, levels state, county, tract
   B01003: kept 5459 rows from 18.3 MB upstream
   ...
  streaming the 2019-2023 ACS geography file for state 36 ...
   geography roster: kept 5474 rows from 91.9 MB upstream
```

5,459 rows is the state row, 62 counties and 5,396 tracts with a published
row. The roster (`Geos20235YR.txt`, from the release's documentation
directory) lists 5,411 tracts: the build carries all of them, and the 15 with
no table row are shown as unavailable with that reason rather than dropped.
A table row the roster does not list stops the build.

Add `--transport api` to use the keyed Census Data API instead. Without
`CENSUS_API_KEY` the command stops with an explanation and a pointer to the
keyless path, rather than failing obscurely.

## 2. Reconcile against an independently published row

```powershell
python -m census_explorer.cli reconcile --fetch --release acs5_2023
```

```json
{
  "checked": 6,
  "matched": 6,
  "results": [
    {"cell": "B01003_001",  "status": "match", "borough_sum": 8516202.0, "published_city_value": 8516202, "difference": 0.0},
    {"cell": "B05002_001",  "status": "match", "borough_sum": 8516202.0, "published_city_value": 8516202, "difference": 0.0},
    {"cell": "B05002_013",  "status": "match", "borough_sum": 3108052.0, "published_city_value": 3108052, "difference": 0.0},
    {"cell": "B05002_003",  "status": "match", "borough_sum": 4112444.0, "published_city_value": 4112444, "difference": 0.0},
    {"cell": "B06004B_001", "status": "match", "borough_sum": 1933195.0, "published_city_value": 1933195, "difference": 0.0},
    {"cell": "B06009_001",  "status": "match", "borough_sum": 6090653.0, "published_city_value": 6090653, "difference": 0.0}
  ],
  "passed": true
}
```

The command exits non-zero on any mismatch. The same run against `acs5_2022`
also reports 6/6.

The statewide check adds every county's published row and compares the total
with the published state row, and first confirms the county roster matches
the release's own list, so a short roster cannot pass by summing fewer
counties:

```powershell
python -m census_explorer.cli reconcile --state --release acs5_2023
```

For 2019-2023 all six cells match exactly across the 62 counties
(for example B01003_001: 19,872,319; B05002_013: 4,499,147). The report is
written to `data/processed/acs5_2023/reconciliation_state.json`.

## 3. Build (offline)

```powershell
python -m census_explorer.cli build --all-releases
```

```
Building 2019-2023 ACS (acs5_2023) from the cache
  join: {"level": "county", "matched": 5, "unmatched_feature_count": 0, "unmatched_observation_count": 0}
  join: {"level": "tract", "matched": 2324, "unmatched_feature_count": 0, "unmatched_observation_count": 3}
  warning: tract @ GENZ2023: 2324 matched, 0 boundary features without observations, 3 observations without a boundary
  dataset written: data/processed/acs5_2023/dataset.json
```

The three unmatched tract observations are `36047990100`, `36081990100` and
`36085990100`: water tracts with a published population of zero that the
cartographic boundary files exclude. They are reported, not dropped.

With the statewide study area now configured, the same build reports (live,
2019-2023, 19.2 s on this machine):

```
  join: {"level": "county", "matched": 62, "unmatched_feature_count": 0, "unmatched_observation_count": 0}
  join: {"level": "tract", "matched": 5395, "unmatched_feature_count": 0, "unmatched_observation_count": 16}
```

The 16 unmatched tract observations are the state's water tracts, each with a
published population of zero: 36011990200, 36013990000, 36029990000,
36047990100, 36055990000, 36059990100, 36059990200, 36059990301,
36059990302, 36059990400, 36063990000, 36073990000, 36075990000,
36081990100, 36085990100 and 36103990100. The New York City three are among
them. Separately, 15 tracts the release lists have no table row at all and
are carried as unavailable, with that reason, not dropped.

## 4. Run the application

```powershell
python -m census_explorer.cli serve
```

```
Census Explorer service on http://127.0.0.1:8765/
  data mode: live   releases: acs5_2022, acs5_2023
  network access is disabled in this process; press Ctrl+C to stop
```

The service refuses to bind to anything but loopback and refuses requests whose
`Host` header is not a loopback name.

In the browser, the journey is:

1. **Choose the area and the geography.** New York City / New York State
   sets the area (shown only when the build covers the state); Boroughs (or
   Counties) / Census tracts sets the level. At tract level a picker lists
   each county, or each borough within the city, with its tract count. The
   scope line above the map always names what the map, table, CSV, brief and
   share link cover, with its size, and offers Reset to New York City. A
   county's place card offers "Explore this county's census tracts".
   Census tracts are statistical areas, not neighbourhoods.
2. **Choose a topic.** The sidebar lists every measure this build carries at
   that level, grouped by the published concept (Population, Born in the U.S. or
   abroad, Citizenship, Birthplace of foreign-born residents, Education by place
   of birth, Race and place of birth). Search by name, or filter to Shares or
   Counts. A name match wins over a description match.
3. **Read the map.** Zoom with the wheel or the `+` / `−` buttons, drag to pan,
   `Fit` to reset. With the map focused, arrow keys pan, `+` and `−` zoom and `0`
   fits. Hovering or focusing an area shows its value and margin of error in the
   readout at the bottom left.
4. **Find a place** with the search above the map — county, borough or
   tract, by published name or by GEOID, anywhere in the build. A place
   outside the current view is labelled so, and its card offers its own
   county's view; at tract level a county appears as "show its N census
   tracts". When more places match than are listed, the list says how many.
   There is no address search, cities are not geographies here, and a search
   that matches nothing says so. The same search filters the table below.
   The table pages through every row (25, 50 or 100 at a time); arrow keys,
   Home/End and Page Up/Page Down move through it with one tab stop.
5. **Inspect a place** by clicking it on the map, clicking or pressing Enter on
   a table row, or picking it from the place search. The right-hand panel gives
   the estimate, the margin of error (or why there is none), the numerator, the
   denominator by name, the reliability wording and the GEOID.
6. **Compare** by adding up to two places to the comparison. A chart draws
   both on one axis from zero with their published 90% margins of error; a
   missing margin, a controlled total and a missing estimate are each drawn
   distinctly. The panel states the difference between the two published
   estimates and says, in the same breath, that this build does not test
   whether that difference is statistically significant.
7. **Add a reference** — New York City built from the five boroughs'
   underlying counts, New York State as the state's own published row, the
   containing county (or borough) when every tract in view sits in one, or
   the selected places combined — or read why one is unavailable.
8. **Scope the output.** "Show only this place" narrows the map, the table, the
   brief and the export together; the scope bar above the map always says what
   the export will cover.
9. **Open the brief** to preview the write-up for what is on screen. It is
   generated on the spot and not saved.
10. **Save and export.** Saving pins the content digests of every input file;
    reopening verifies them and refuses rather than showing different numbers.
    Export writes `brief.html`, `data.csv`, `provenance.json` and `figure.svg`
    for exactly the areas in scope, and the panel that appears links each one:
    open it in a tab or save a copy, with no path to copy into a terminal.
    Those links are served read-only from the `artifacts/` directory and from
    nowhere else.

"Method & sources" opens a drawer with what is being shown, the three quality
statements, what the view does not say, and the citation. Published table codes
stay in a details panel in the right-hand column.

## 4b. Build the published static copy (offline)

```powershell
python -m census_explorer.cli site build --base /census-explorer/ --out site
```

Writes `site/`: the interface plus everything the Python computed, as files a
browser reads over relative URLs. The explore journey works with no service
behind it; the brief, the export bundle and saved views are service features
and are disabled on the page with reasons. `docs/DEPLOY.md` has the full
contents list and the publishing steps.

## 5. Verify (all offline)

### The test suite

```powershell
python -m unittest discover -s tests -t .
```

```
Ran 174 tests in 8.0s
OK (skipped=3)
```

The three skips are the live smoke tests, which are opt-in. Coverage by concern:

| Concern | Where |
| --- | --- |
| Sentinel and annotation handling, missing vs zero | `tests/test_sentinels.py` |
| Estimate/MOE pairing, aggregation, proportion formulas, zero denominators, CV limits | `tests/test_measures.py` |
| GEOID typing, duplicate and missing geography, join accounting, shapefile reading | `tests/test_geography.py` |
| Metadata resolution, unknown cells, variable drift between releases, cache corruption and truncation | `tests/test_metadata_and_manifest.py` |
| Summary File and API readers, malformed and duplicate rows, transport agreement | `tests/test_dataset.py` |
| Reconciliation mismatch reporting | `tests/test_dataset.py` |
| Comparison rules, period labels, shared cut points | `tests/test_compare.py` |
| Configuration validation, saved projects and schema versioning | `tests/test_config_and_projects.py` |
| Credential redaction in logs, manifests, errors and every endpoint | `tests/test_redaction.py` |
| Build → service → project replay → export, over real HTTP | `tests/test_end_to_end.py` |
| Pinned replay: changed values, changed definitions, changed geometry, missing inputs, exact replay | `tests/test_review_regressions.py` |
| Boundary-vintage evidence: missing, wrong level, moved geometry, identical geometry | `tests/test_review_regressions.py` |
| Semantic drift: substantive label change, reviewed restyling, per-cell universe swap, missing metadata | `tests/test_review_regressions.py` |
| Export scope matching the figure; multi-measure validation | `tests/test_review_regressions.py` |
| Output-path containment and cross-origin POST refusal | `tests/test_review_regressions.py` |

### Cache integrity

```powershell
python -m census_explorer.cli verify manifests
```

```
  geography-acs5_2022-...json: 2 artifacts, OK
  geography-acs5_2023-...json: 2 artifacts, OK
  metadata-acs5_2022-...json: 5 artifacts, OK
  metadata-acs5_2023-...json: 5 artifacts, OK
  observations-summary-file-acs5_2022-...json: 5 artifacts, OK
  observations-summary-file-acs5_2023-...json: 5 artifacts, OK
  reconcile-acs5_2022-...json: 4 artifacts, OK
  reconcile-acs5_2023-...json: 4 artifacts, OK
manifest verification: OK
```

### Catalog against the published release

```powershell
python -m census_explorer.cli verify catalog --release acs5_2023
```

```
47 measures checked against 2019-2023 ACS: OK
```

### Comparison compatibility

`--measure` is required: semantic compatibility cannot be established without
one, and a check that cannot run blocks rather than being skipped.

```powershell
python -m census_explorer.cli compare --a acs5_2023 --b acs5_2022 --level county --measure foreign_born_share
```

```json
{
  "allowed": true,
  "semantic_verified": true,
  "shared_geoids": 5,
  "comparable_geoid_count": 5,
  "independent_observations": false,
  "geography_evidence": {
    "kind": "computed_geometry",
    "established": true,
    "areas_compared": 5,
    "max_relative_area_difference": 0.000594,
    "max_centroid_shift_metres": 5.1
  }
}
```

The same command at `--level tract` exits non-zero and reports:

```
geographic comparability across boundary vintages GENZ2023 and GENZ2022 at
tract level has not been established: 71 of 2324 shared tract areas differ
beyond the documented tolerance (worst relative area difference 0.073280,
worst centroid shift 344.1 m). A shared identifier is not evidence that these
are the same area; a reviewed equivalence record or a validated harmonisation
is required.
```

That is the intended outcome, not a failure of the command. See
`DATA_HANDLING.md` for the rule and what to do about it.

Comparing a five-year release with a one-year release is blocked for a
different reason, and a substantive label change blocks for a third.

### Saved projects

```powershell
python -m census_explorer.cli project list
python -m census_explorer.cli project verify --id nyc-foreign-born-boroughs
```

```
8 pinned input(s): OK
```

`verify` exits non-zero and names the file if any pinned input has changed or
gone missing. Reopening such a project in the browser is refused with the same
message rather than showing different numbers.

### Scoped export

```powershell
python -m census_explorer.cli export --release acs5_2023 --measure foreign_born_share `
  --level county --areas 36005,36047 --figure chart
```

The CSV and the figure cover the same two boroughs. Adding
`--compare-with acs5_2022` produces a two-panel map on shared class breaks with
the overlap disclosure printed on the figure.

## 6. The live smoke test (separate and opt-in)

```powershell
$env:CENSUS_EXPLORER_LIVE = "1"
python -m unittest tests.test_live_smoke -v
```

```
test_table_metadata_is_reachable_without_a_credential ... ok
test_five_boroughs_return_a_usable_population_estimate ... ok
test_keyed_api_returns_the_same_borough_totals_as_the_summary_file ... skipped 'needs CENSUS_EXPLORER_LIVE=1 and CENSUS_API_KEY'
```

With a key configured, the third test also runs and asserts that both
transports return identical borough totals and that the key appears in no
manifest record.

There is also a one-command version:

```powershell
python -m census_explorer.cli smoke --transport summary-file
```

## 7. Fixture mode

```powershell
python -m census_explorer.cli fixtures build
python -m census_explorer.cli serve --data-dir data/fixture-processed
```

Every page, figure and export is banner-marked `FIXTURE MODE`, the shapes are
labelled `SYNTHETIC SHAPES — not boundaries`, and area names begin with
`FIXTURE`. Two of the five fixture areas deliberately exercise the unavailable
paths: one returns an annotation instead of a value, and one has a zero
denominator.

Fixture values are synthetic. They are not census estimates and must not be
quoted as findings.

## Repository hygiene

```powershell
python -m json.tool config/project.json
python -m json.tool config/measures.json
git check-ignore .env data/raw/example.csv.gz artifacts/example.html
git status --short --branch
```
