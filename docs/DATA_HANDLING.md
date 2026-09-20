# Data handling decisions

Every decision here is one that could quietly corrupt a result if made badly.
Each is recorded with what was chosen, why, and what the code does about it.

## Release, period and geography vintage are one key

A dataset is identified by product, reference period **and** boundary vintage
together (`acs/acs5|2019-2023|geo:GENZ2023`). A 2019-2023 ACS five-year estimate
is labelled `2019-2023 ACS` everywhere, and configuration that labels a
five-year period with a single year is rejected when the configuration loads.
There is no code path that can render such an estimate as "2020".

Standard 2020 ACS one-year estimates were not released, and the experimental
estimates that replaced them are a different product. Neither is retrieved here.

## Transports

Two retrieval paths produce the same published values:

| Transport | Credential | Notes |
| --- | --- | --- |
| Table-based Summary File | none | The default. One large pipe-delimited file per table covering every published geography; streamed and filtered to the requested GEO_IDs. |
| Census Data API | `CENSUS_API_KEY` | Small targeted JSON responses; the key is stripped from everything recorded. |

Census metadata endpoints need no key at all, so the exact estimate,
margin-of-error and annotation variable names, their labels and their universes
are always read from the release itself. No cell code in this repository was
written from memory; `verify catalog` re-checks all 47 measures against the
published release, and `compare` reports any label, universe or availability
drift between releases.

The Summary File is streamed rather than stored whole. The cache keeps the
header and the matching data lines byte-for-byte; the manifest records the
SHA-256 and byte length of the **complete** upstream document, so the download
can be re-verified later. Different geographic selections of the same table are
cached under different names: a narrower pull can never overwrite a wider one.

## Annotation values are meanings, not numbers

The Census Bureau publishes specific negative integers in numeric fields to
carry a meaning. The table of those values is not hard-coded from memory: it is
extracted from the official developer documentation and stored in
`census_explorer/reference/acs_annotation_values.json` with the source URL, the
retrieval timestamp and the document's checksum. Refresh it with
`reference refresh`.

Every source value is classified before anything arithmetic happens to it:

| Status | Meaning |
| --- | --- |
| `ok` | a number, including a genuine zero |
| `annotated` | a documented annotation; the published meaning travels with it |
| `missing` | absent or empty in the source |
| `unparseable` | present but neither a number nor a documented annotation |

Only `ok` carries a value. An undocumented nine-digit negative repunit (say
`-444444444`) is refused as `unparseable` rather than used, on the assumption
that it is a new annotation rather than a measurement.

### The one arithmetic exception, stated openly

`-555555555` means the estimate is controlled to an independent population
estimate and has no sampling error, and the published documentation says its
margin of error "may be treated as zero". This is applied as published: the
cell contributes zero variance rather than making the result unavailable. It is
not silent. The value carries a `controlled estimate (MOE treated as zero)`
flag that appears in the detail drawer, in the `source_flags` column of every
export, and in the figure's data. Every other annotation makes the margin of
error unavailable.

Without this rule, almost every borough-level share would have no margin of
error at all, because the ACS controls county population totals.

## Estimates and margins of error are tracked separately

Availability of an estimate and availability of its margin of error are
independent. ACS routinely publishes a real estimate with an annotated margin
of error, and the reverse can happen too. A measure value therefore carries
`estimate_status` and `moe_status` independently, each with its own stated
reason. Nothing substitutes zero for either.

## Aggregation and shares

Counts are summed and their margins of error combined in quadrature. Shares are
computed from the summed numerator over the summed denominator — percentages
are never summed, and area medians are never averaged.

The margin of error on a share uses the published ACS approximations: the
proportion formula where the numerator is a subset of the denominator, falling
back to the ratio formula when its radicand is negative. The formula that was
used is recorded on the value and shown in the drawer.

A zero denominator makes a share **undefined, not zero**, with that reason
stated.

## Coefficients of variation

A CV is computed only where it is defined: a count, with a valid numeric 90%
margin of error, and a non-zero estimate, using the documented `SE = MOE / 1.645`
relationship. Shares get their margin of error shown instead. No CV is computed
for a median, a microdata estimate or any statistic without a documented
design-appropriate variance method.

## Universes and denominators

A denominator is part of a measure's definition, not a display toggle.
Configuration that declares a share without a denominator, or whose numerator
and denominator come from different published universes, is rejected at load.

Two universe subtleties in this catalog are handled rather than smoothed over:

- **B05006** counts the *foreign-born population excluding population born at
  sea*, which is narrower than the foreign-born population in **B05002**.
  B05006 shares therefore use `B05006_001` as their denominator, not
  `B05002_013`, and the difference between the two published totals is reported
  as a diagnostic rather than reconciled away.
- **B06009** and **B06004B** are restricted to people living in the United
  States, and B06009 to adults 25 and over. Those universes are shown with
  every value.

## Birthplace is a stock

Every place-of-birth measure states in the interface and in every export that
it counts residents *born* somewhere, not people who recently arrived. Nothing
in this repository infers a migration flow from a birthplace count, or an
outflow from a falling stock.

"Born in state of residence" is labelled **Born in New York State**. It does
not identify people born in New York City, and the caveat says so on every such
measure. Puerto Rico and the other U.S. island areas are native-born territory
of birth and appear under B05002's "Native; born outside the United States",
not among the foreign born.

`B06004B` crosses place of birth with race only at the broadest birthplace
level. It cannot identify Caribbean-born Black residents, and the measure says
so; that intersection is not published in B05006 or B06004B.

## Geography

Boundary vintage is part of the dataset key: the 2019-2023 ACS five-year tables
are joined to GENZ2023 cartographic boundaries, and the 2018-2022 tables to
GENZ2022.

GEOIDs are strings. Passing an integer raises, because a lost leading zero is a
county that silently moves. Duplicate identifiers on either side of a join are
a hard error rather than a row multiplier. Every join produces a report with
matched counts, unmatched boundary features, unmatched observations by
identifier, and the total population of the unmatched observations.

In the current NYC build, three tract observations per release have no boundary
feature: `36047990100`, `36081990100` and `36085990100`. These are water tracts
with a published population of zero, which the cartographic boundary files
exclude. The join report states this rather than dropping them.

## Reconciliation against an independent published row

New York City as a place (GEOID 3651000) is exactly the five boroughs, and the
Census Bureau publishes it as its own row in the same table. `reconcile` sums
the five county rows and compares them with that published city row.

Both releases currently match exactly on all six checked cells:

| Cell | Borough sum, 2019-2023 ACS | Published city row |
| --- | --- | --- |
| `B01003_001` total population | 8,516,202 | 8,516,202 |
| `B05002_001` total population | 8,516,202 | 8,516,202 |
| `B05002_013` foreign-born | 3,108,052 | 3,108,052 |
| `B05002_003` born in state of residence | 4,112,444 | 4,112,444 |
| `B06004B_001` Black alone | 1,933,195 | 1,933,195 |
| `B06009_001` population 25 and over | 6,090,653 | 6,090,653 |

A mismatch is reported with its size and fails the command. It is never
absorbed.

## Comparison rules

| Situation | Behaviour |
| --- | --- |
| Different survey products (one-year vs five-year) | **Blocked** |
| Different period lengths | **Blocked** |
| A release compared with itself | **Blocked** |
| No shared geographic identifiers | **Blocked** |
| Overlapping five-year periods | Allowed, with a mandatory disclosure that they are not independent observations and must not be presented as a tested change |
| Different boundary vintages | Allowed, restricted to identifiers present in both, with the difference reported |
| A published cell label that changed between releases | Allowed, with the change quoted |
| Areas present in one release only | Allowed, excluded from the comparison and listed |

Where a comparison is drawn, both panels use one set of class breaks computed
over the pooled values of both periods. Independent quantile legends on each
side can manufacture the appearance of change.

The default comparison, 2019-2023 ACS against 2018-2022 ACS, shares four years.
The interface prints that fact above the maps, the export records it in
`provenance.json`, and the figure prints it under the chart.

## Provenance

Every retrieved artifact has a manifest record: provider, kind, sanitized
source URL, sanitized request, retrieval timestamp, HTTP status, byte length,
SHA-256, and cache path. `verify manifests` re-hashes every artifact and
reports checksum, size and existence failures. A saved project pins the
manifest identifiers it was built from, so refreshing the cache cannot silently
change an older figure.

Every export bundle carries `data.csv`, `provenance.json` (releases, measure
definitions with cells and universes, all manifests, join accounting,
diagnostics and any comparison rules that applied), `figure.svg` and a
`README.txt` explaining how to read the columns.

## Credentials

The key is read from the environment at the moment of a call. It is never
written to a cache file, a manifest, an export, a log line or an error message,
and the URL recorded in provenance has the credential parameter removed
entirely, not masked. The local service never reads a credential at all, so the
browser cannot receive one. Tests assert this against every endpoint and both
export files.
