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
| Topic sidebar over every measure the build carries, grouped by published concept | Working; 47 measures at borough and tract level |
| Plain-language selection summary: what is counted, out of what | Working |
| Compatible benchmark built by adding underlying counts | Working; a named composite is refused unless its whole documented membership is present |
| Quality as three separate statements, no combined score | Working |
| Print-ready brief from the same validated selection | Working |
| ACS retrieval for the five boroughs and all NYC census tracts | Working, 2019-2023 and 2018-2022 ACS five-year |
| Exact cells, labels and universes resolved from official release metadata | Working; no code is written from memory |
| Immutable raw cache, checksums, retrieval timestamps, manifests | Working; `verify manifests` re-hashes every artifact |
| Reconciliation against an independently published official row | Working; 6/6 cells match exactly in both releases |
| Matching-vintage Census polygons, string GEOIDs, join accounting | Working; boroughs and 2,324 tracts |
| Local browser app: map workspace, topic sidebar, place search, inspect and compare panel, sortable table | Working |
| Saved projects, CSV + provenance export, SVG figure export | Working |
| Comparing two places within one period | Working; needs no boundary equivalence, and no significance is claimed |
| Comparing two reference periods | **Blocked at every level.** Equivalence across boundary vintages needs documented provider correspondence or a scoped review, and this repository ships neither |
| Saved projects that reproduce exactly or refuse to open | Working; content-pinned and fail-closed |
| Offline test suite | Working: 370 tests, no network |
| Fixture mode for machines with no data and no credentials | Working, conspicuously labelled |
| Static build for GitHub Pages | Working; the explore journey runs with no Python behind it, with shareable views and printable briefs; saved projects and export bundles still require the local app. See `docs/DEPLOY.md` |
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

## Publishing a static copy

```powershell
python -m census_explorer.cli site build --base /census-explorer/ --out site
```

Runs this repository's Python once and writes `site/` — the interface plus
everything it computed, as files a browser reads over relative URLs, with no
service behind them. The explore journey, shareable links and printable briefs
work there. Links check the published data snapshot; briefs list up to 25 selected
places and CSV includes the full scope. Export bundles and saved projects
still need the local service. See
[docs/DEPLOY.md](docs/DEPLOY.md).

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
python -m unittest discover -s tests -t .          # 280 offline tests
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
  and the export. That claim is recorded when the value is computed, never
  inferred later: a controlled denominator does not make a share controlled,
  and a margin of error that could not be computed stays unknown.
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
- **The sidebar offers what the build carries, and nothing else.** A measure is
  listed at a geography level only when the build recorded it as available
  there, and a measure the sidebar lists always resolves to a question the
  brief can render — a test asserts both, in both directions.
- **A difference is a difference, not a finding.** Two places can be compared
  inside one reference period, and the panel states the arithmetic difference
  next to both margins of error. It does not test significance and says so.
- **A search that finds nothing says what this build can find.** There is no
  address search and no neighbourhood geography here; the empty result says
  that rather than leaving a blank box.
- **Only the newest load may change what is on screen.** Controls are faster
  than the service, so several loads can be in flight at once. An older
  response, success or failure, is dropped rather than committed, and saving
  and exporting stay unavailable until one complete load has landed — neither
  may describe a view assembled from two of them.
- **The legend describes the map in front of you.** The number of shaded
  classes comes from the values in view: one borough, or a set of areas that
  share a value, is one class and says so. Every break is a value that occurs
  in the data, and every class is one at least one area falls in.
- **Coverage is stated for the selection, not the build.** How many areas in
  view could not be drawn is separate from how many the whole build cannot
  draw, and a view that drew everything does not inherit the second number.
- **A starting example is a starting point, not a finding.** Each of the
  three cards borrows the catalog's own wording for what its measure counts
  and what it is out of, names the places and period it opens, and says what
  the view does not say. A card whose measure or place this build does not
  carry is dropped rather than adjusted to something else.
- **A published copy computes nothing.** The static build runs this
  repository's Python once and writes what it produced; the page reshapes and
  counts, and a round-trip test compares its uncertainty panels and its CSV
  against the service's own output, case by case. What it cannot do — the
  export bundle, saved views and their raw-input pin check —
  is disabled on the page with the reason, never approximated.
- **An export is delivered, not announced.** The panel links the brief, the
  data, the figure and the provenance record; the route that serves them is
  read-only, confined to `artifacts/`, and limited to the file types an
  export writes.

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
- [Publishing](docs/DEPLOY.md): the static build, exactly what it contains,
  what a published copy cannot do, and the steps to put it on GitHub Pages.
- [Release checklist](docs/RELEASE_CHECKLIST.md): what has to be true before
  publishing, what is true now, and what is still open.
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
