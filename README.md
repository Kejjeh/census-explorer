# Census Explorer

**Turn a question about a place into a defensible brief someone can
understand, check and reuse.**

A local, offline-first tool for official census data. Pick one of three
starting questions, pick the place, pick what to show, and get a print-ready
brief with the definitions, the denominators, the margins of error and the
sources attached — plus the CSV and the provenance bundle behind it. New York
City is the first project; the data engine is independent of it.

Who it is aimed at, and whether anyone would pay for it, are **hypotheses**.
See [docs/PRODUCT_DIRECTION.md](docs/PRODUCT_DIRECTION.md).

Everything runs on your own machine. There is no account, no hosted service and
no telemetry, and the local service holds no credentials.

## What works today

| Capability | State |
| --- | --- |
| Three guided questions mapped to validated measures | Working |
| Plain-language selection summary: what is counted, out of what | Working |
| Compatible benchmark built by adding underlying counts | Working |
| Quality as three separate statements, no combined score | Working |
| Print-ready brief from the same validated selection | Working |
| ACS retrieval for the five boroughs and all NYC census tracts | Working, 2019-2023 and 2018-2022 ACS five-year |
| Exact cells, labels and universes resolved from official release metadata | Working; no code is written from memory |
| Immutable raw cache, checksums, retrieval timestamps, manifests | Working; `verify manifests` re-hashes every artifact |
| Reconciliation against an independently published official row | Working; 6/6 cells match exactly in both releases |
| Matching-vintage Census polygons, string GEOIDs, join accounting | Working; boroughs and 2,324 tracts |
| Local browser app: catalog, map, sortable table, detail drawer | Working |
| Saved projects, CSV + provenance export, SVG figure export | Working |
| Comparing places within one period | Working; needs no boundary equivalence |
| Comparing two reference periods | **Blocked at every level.** Equivalence across boundary vintages needs documented provider correspondence or a scoped review, and this repository ships neither |
| Saved projects that reproduce exactly or refuse to open | Working; content-pinned and fail-closed |
| Offline test suite | Working: 210 tests, no network |
| Fixture mode for machines with no data and no credentials | Working, conspicuously labelled |
| Historical microdata, generations, migration flows, full platform parity | **Not started.** See `docs/RESEARCH_PLAN.md` |

Stages A, B and C of `docs/SOCIAL_EXPLORER_RESEARCH.md` are implemented. Stages
D and E (historical evidence and specialised research) are not, and nothing in
this repository should be read as a finding about NYC's demographic history.

## Requirements

Python 3.11 or newer. **No third-party packages.** The project deliberately uses
only the standard library, and the browser interface is plain HTML, CSS and
JavaScript with no CDN, so it works with no network at all. See
`docs/DEPENDENCIES.md` for the DuckDB / Parquet / MapLibre decision that is
still open.

## Quick start

No credential is needed for the default path: the Census Bureau publishes the
same ACS estimates in its table-based Summary File, which is open.

### Windows PowerShell

```powershell
cd census-explorer
python -m census_explorer.cli fetch all --release acs5_2023
python -m census_explorer.cli reconcile --fetch --release acs5_2023
python -m census_explorer.cli build --release acs5_2023
python -m census_explorer.cli serve
```

Then open <http://127.0.0.1:8765/>.

To add the comparison period:

```powershell
python -m census_explorer.cli fetch all --release acs5_2022
python -m census_explorer.cli reconcile --fetch --release acs5_2022
python -m census_explorer.cli build --release acs5_2022
```

### macOS / Linux

The same commands run unchanged:

```bash
python3 -m census_explorer.cli fetch all --release acs5_2023
python3 -m census_explorer.cli reconcile --fetch --release acs5_2023
python3 -m census_explorer.cli build --release acs5_2023
python3 -m census_explorer.cli serve
```

`fetch` streams roughly 440 MB per release from the Census Bureau and keeps only
the rows for the requested geographies (about 3.5 MB), recording the digest of
the complete upstream file so the download can be re-verified. It takes well
under a minute on a reasonable connection.

### No data yet? Run fixture mode

```powershell
python -m census_explorer.cli fixtures build
python -m census_explorer.cli serve --data-dir data/fixture-processed
```

Fixture mode starts the whole application with synthetic values and synthetic
rectangles instead of boundaries. Every screen, figure and export is banner-
marked `FIXTURE MODE`. **Nothing produced in fixture mode is a census finding.**

## Credentials

The default Summary File transport needs no key. The Census Data API does, and
it is supported as an alternative transport:

```powershell
# Request your own key at https://api.census.gov/data/key_signup.html
$env:CENSUS_API_KEY = "<your key>"           # current session only
python -m census_explorer.cli fetch observations --release acs5_2023 --transport api
python -m census_explorer.cli build --release acs5_2023 --transport api
```

```bash
export CENSUS_API_KEY="<your key>"
python3 -m census_explorer.cli fetch observations --release acs5_2023 --transport api
```

Rules the code enforces:

- The key is read from the environment at the moment of the call and is never
  written to a cache file, manifest, export, log line or error message. The
  recorded URL has the `key` parameter removed entirely.
- The key never reaches the browser, because the local service never reads one.
- `.env` is git-ignored; `.env.example` documents the variable names only.
- Never paste a key into a chat window, an issue, or a commit.

## Commands

| Command | Network | What it does |
| --- | --- | --- |
| `fetch metadata` | yes | Official table metadata (no key required) |
| `fetch geography` | yes | Cartographic boundary files of the matching vintage |
| `fetch observations` | yes | ACS estimates and margins of error |
| `fetch all` | yes | All three, in order |
| `reference refresh` | yes | Re-archive the official annotation-value documentation |
| `reconcile --fetch` | yes | Retrieve the published New York City row and compare it with the borough sum |
| `smoke` | yes | A deliberately small live request, run on its own |
| `build` | no | Assemble the analysis dataset from the cache |
| `verify manifests` | no | Re-hash every cached artifact against its manifest |
| `verify catalog` | no | Check every measure against the published release |
| `catalog list \| show` | no | Search the catalog; show a measure's exact cells |
| `compare --a --b --measure` | no | Report whether two releases may be compared, and why not |
| `reconcile` | no | Re-run the comparison from the cache |
| `export` | no | Write a CSV + provenance + figure bundle to `artifacts/` |
| `project list \| show \| verify \| delete` | no | Saved project definitions and their pinned inputs |
| `fixtures build` | no | Build the synthetic fixture dataset |
| `serve` | no | Run the local browser application on loopback |

Retrieval is always an explicit command. Importing the package, running the
tests, building the dataset, serving the app and rendering a figure never touch
the network; the service switches network access off for its own process at
start-up.

## Verify the build

```powershell
python -m unittest discover -s tests -t .          # 210 offline tests
python -m census_explorer.cli verify manifests     # re-hash the raw cache
python -m census_explorer.cli verify catalog       # cells vs the published release
python -m census_explorer.cli reconcile --release acs5_2023
python -m census_explorer.cli compare --a acs5_2023 --b acs5_2022 --level county --measure foreign_born_share
python -m census_explorer.cli project verify --id <project-id>
```

The live smoke test is opt-in and separate:

```powershell
$env:CENSUS_EXPLORER_LIVE = "1"
python -m unittest tests.test_live_smoke
python -m census_explorer.cli smoke --transport summary-file
```

The keyed API smoke test additionally needs `CENSUS_API_KEY` and skips without
one rather than failing.

## What the interface insists on

- **Period labels are never shortened.** A 2019-2023 ACS five-year estimate is
  labelled `2019-2023 ACS` everywhere, including exports and figures.
  Configuration that labels a five-year period with a single year is rejected
  at load time.
- **A denominator is part of a measure.** Every share names its numerator
  cells, denominator cells and published universe, in the interface and in
  every exported row.
- **Unavailable is not zero.** Census annotation codes such as `-999999999`
  are classified as meanings, never parsed as numbers, and are shown as
  "no data" with the published reason. They sort to the end of the table.
- **Missing uncertainty is unavailable.** The one exception is the documented
  `-555555555` annotation, which states that a controlled estimate has no
  sampling error; it is applied as published and always flagged in the drawer
  and the export.
- **Birthplace is a stock, not a flow.** No measure describes a place of birth
  as a recent arrival, and "born in state of residence" is labelled as New York
  *State*, never New York *City*.
- **Comparisons are refused by default.** A comparison is drawn only when every
  applicable check has actually run and passed. Different survey products or
  period lengths are blocked. A comparison across boundary vintages is blocked
  until equivalence is established by the same vintage, documented provider
  correspondence, or a scoped review — a shared GEOID is not evidence, and
  neither is a computed resemblance between two polygons. A published cell
  label that changed in a way nobody has reviewed is blocked, not disclosed and
  drawn anyway. A check that could not be performed — no measure named,
  metadata not cached — blocks rather than being skipped.
- **A question offers nothing the data cannot support.** No income, poverty,
  housing or rent: those tables are not in this build, so no question mentions
  them. No neighbourhood names: there are no documented neighbourhood
  boundaries here, so a census tract is called a census tract.
- **Quality is three statements, not a score.** Uncertainty, comparison
  eligibility and reference period are different problems. There is no combined
  trust number, no "significant change" claim, and no causal language.
- **A saved project reproduces exactly, or refuses to open.** It pins the
  content digests of the files it was built from — the measure values, the
  geometry, the dataset's measure definitions and the retrieval manifests — and
  replays from its own pinned definitions rather than the current catalog. If
  any pinned input is missing or changed, replay and export stop with a message
  naming the file. Nothing is re-fetched and nothing is substituted.
- **One selection drives every output.** The table, both map panels, the chart,
  the CSV and the provenance document are built from a single validated
  selection, so an exported figure cannot cover areas the exported data does
  not.
- **Joins are accounted for.** GEOIDs are strings, duplicates are refused, and
  unmatched features and observations are reported by identifier and by
  population.
- **Research codes stay in a details panel**, not in the primary navigation,
  and travel with every export.

## Layout

```
census_explorer/     ingestion, validation, measures, service (standard library only)
  retrieve/          provider-specific retrieval; the only modules that fetch
  reference/         annotation semantics extracted from official documentation
web/                 the browser interface: plain HTML, CSS and JavaScript
config/              project scope, releases, and the measure catalog
fixtures/            small synthetic inputs used by the offline tests
tests/               the offline test suite
data/                git-ignored: raw cache, manifests, processed datasets, projects
artifacts/           git-ignored: export bundles
```

## Documentation

- [Runbook](docs/RUNBOOK.md): fresh checkout to a finished brief, with the
  expected output and timings at each step.
- [Product direction](docs/PRODUCT_DIRECTION.md): the promise, what is
  hypothesis, and the roadmap as candidates rather than commitments.
- [Usage and verification](docs/USAGE.md): every command, with expected output.
- [Data handling decisions](docs/DATA_HANDLING.md): universes, denominators,
  annotations, margins of error, geography and comparison rules.
- [Dependencies](docs/DEPENDENCIES.md): what is used, and the open decision.
- [Social Explorer research](docs/SOCIAL_EXPLORER_RESEARCH.md): the feature
  comparison and staged plan this implementation follows.
- [Research plan](docs/RESEARCH_PLAN.md): the eight research directions and
  their evidence gates. **None of them is complete.**
- [Sources](docs/SOURCES.md): official documentation, and claims still unverified.
- [Agent instructions](AGENTS.md): the review rules for changes here.

## Limitations, stated plainly

- Only NYC counties and tracts, and only the two ACS five-year releases listed
  in `config/project.json`, have been retrieved and validated.
- **Comparing two reference periods is blocked at every level.** Equivalence
  across boundary vintages requires documented provider correspondence or a
  scoped review, and this repository ships neither. Measuring the published
  footprints (`cli geography footprint`) shows why the question is real: 73 of
  2,324 shared tracts and 3 of 5 counties differ beyond the measurement
  tolerance between GENZ2022 and GENZ2023. That measurement informs a review;
  it cannot replace one. Comparing **places within one period** is unaffected.
- **A saved brief detects tampering; it is not an archive.** It pins the digest
  of every input and refuses to reopen if one changed. It does not keep a copy
  of the data and cannot restore an earlier version. Durable versioned briefs
  are on the roadmap, not in this build.
- The measure catalog is 47 measures across five tables. It is not a
  500,000-variable library and does not attempt platform parity.
- The map uses local boundary layers and a simple equirectangular projection
  suitable for one city. There is no basemap, no tile pipeline and no
  ring/drive-time analysis.
- No historical microdata, no IPUMS or NHGIS extract, no generation analysis, no
  migration flows and no pre-2018 series exist in this repository. The eight
  research directions in `docs/RESEARCH_PLAN.md` remain open, and their
  evidence gates have not been met.
- This is a personal research tool, not a production system. It has had no
  security review, no load testing and no multi-user design.
