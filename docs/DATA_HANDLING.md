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
that it is a new annotation rather than a measurement. `NaN`, `Infinity` and
their variants are refused too: `float()` accepts all of them, and one reaching
a sum, a class break or an exported cell would corrupt it silently.

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

## Boundary-vintage equivalence

Two releases may only be compared area by area when the areas are established
to be the same ground. A shared GEOID is not that evidence: identifiers are
reused across vintages even when a boundary moves.

The rule, its tolerances and any reviewed records live in
`config/geography_equivalence.json`:

- **Same boundary vintage** — equivalence holds by construction.
- **Different vintages** — equivalence must be established either by computed
  geometry evidence (every shared area's polygons compared between the two
  cached boundary files, all within tolerance) or by a reviewed equivalence
  record naming both vintages, the level, the evidence, the reviewer and the
  date.
- **Neither** — the comparison is blocked.

The tolerances are 0.1% of area and 25 metres of centroid movement. A
difference inside them is recorded as a cartographic generalisation difference
and reported; it is not treated as demographic change. A difference outside
them blocks until a human looks at it. Neither branch assumes an equal
identifier means a stable area.

An area that lacks geometry in either vintage is not comparable even when both
releases publish an observation for it, and is excluded and listed.

### What this found in the current NYC build

| Level | Areas compared | Worst area difference | Worst centroid shift | Verdict |
| --- | --- | --- | --- | --- |
| County | 5 | 0.000594 (0.06%) | 5.1 m | established |
| Tract | 2,324 | 0.0733 (7.3%) | 344.1 m | **not established**, 71 areas fail |

So the borough comparison is drawn on computed evidence, and the tract
comparison is refused. The 71 failing tracts are mostly small waterfront areas
whose median size is 0.72 km²; the differences are plausibly cartographic, but
plausibility is not evidence. To enable the tract comparison, examine them and
add a record to `config/geography_equivalence.json`. Widening the tolerance to
make the check pass would defeat its purpose.

## Semantic compatibility

A comparison also has to mean the same thing in both releases, and this check
cannot be skipped: if the inputs needed to perform it are unavailable, the
comparison is refused rather than allowed on the strength of the checks that
did run. No measure named, metadata not cached, or a cell absent from one
release all block.

Universes are compared **cell by cell**, not as sets. Two cells exchanging
universes leaves the set of universes unchanged while changing what the measure
computes.

A published label that changed is classified, not merely reported:

- If normalising case, whitespace, a trailing colon, hyphen-versus-space,
  typographic punctuation or abbreviation dots makes the two labels identical,
  it is a restyling a human has already reviewed. It is allowed and disclosed.
  `config/label_semantics.json` records which restylings those are and when
  they were reviewed.
- If the exact change is listed under `reviewed_equivalences` in that file,
  the reviewer's note is quoted and the comparison proceeds.
- Otherwise it **blocks**. `B05002_013` going from `Foreign-born` to
  `Foreign born` is the first kind. A cell relabelled to a different concept is
  the second, and no amount of disclosure makes it comparable.

Every requested measure is validated, not only the first, so an incompatible
measure cannot ride along with a compatible one in an export.

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

## One selection, every output

The table, both map panels, the chart, the CSV and the provenance document are
built from a single `Selection`, resolved and validated once: the releases, the
level, the measures, the exact areas and the class breaks. An export of one
borough produces a figure of one borough. A comparison export renders both
periods on the shared breaks and prints the overlap disclosure, rather than
showing the primary panel alone.

When a comparison is active the selection narrows to the areas the comparison
is actually allowed to use, so "areas present in only one release are excluded"
is true of the data and the figure, not only of the sentence.

A chart with one row per area stops being readable long before a city's worth
of tracts, so above 25 rows it draws the highest-ranked and says so on the
figure and on screen. The exported CSV always contains every selected area.

## Pinned inputs and replay

A saved project records the content digests of the exact files it was built
from — the measure values, the geometry, the dataset's measure definitions and
universes, and the retrieval manifests behind them — together with the measure
and release definitions as they were at the time.

Recording a manifest identifier alone would not have protected anything:
identifiers stay the same while bytes move underneath them. The digests are
what make the promise real.

Reopening a project verifies every pinned input first. If one is missing or its
content has changed, replay and export stop with a message naming the file, the
pinned digest and the current one. Nothing is re-fetched and nothing is
substituted. The remedy is stated: restore the files, or open the view you want
now and re-save.

Replay also uses the project's own pinned definitions rather than the current
catalog, so editing a measure in `config/measures.json` cannot silently change
what an older project means.

`project verify --id <id>` runs the same check from the command line.

## Output containment and request hardening

Export bundles are written only inside `artifacts/`. The bundle name must be a
single path component of ordinary characters; anything with a separator, a
parent reference, a drive letter, a NUL or a Windows reserved device name is
refused rather than sanitised, and the resolved destination is checked to be
inside `artifacts/` before anything is created. The check runs again inside
`write_bundle`, so a caller that builds a path another way cannot escape
either.

Release identifiers, measure identifiers and geography levels that become part
of a file path are validated before use, and the static file handler tests
containment with `Path.is_relative_to` rather than a string prefix.

State-changing requests must carry `Content-Type: application/json`, which a
cross-origin page cannot set without a CORS preflight this service never
answers, and their `Origin` must be this service's own if one is present. A
loopback `Host` header alone would not have stopped a cross-origin simple POST
to localhost.

## Named composites must be the whole place

A reference called "New York City" has to be New York City. `config/project.json`
records the composite's documented membership — the five county GEOIDs — and a
benchmark carrying that name is built only when all five are present and usable.

Three ways it refuses, rather than quietly rebasing on whatever is available:

- a member area is not in the built dataset: the reference is unavailable and
  names the missing boroughs;
- a member has no usable value: the reference is unavailable and names it;
- the configured county set no longer matches the documented membership: the
  reference is unavailable and prints both sets.

The last case matters because a project narrowed to two boroughs may be
perfectly good work; its totals are simply not the city's. Summing one borough
and labelling it "all five boroughs" is not a smaller New York City, it is a
wrong number with an authoritative name on it.

Arithmetic exactness is not accuracy. The sum of counts is exact as arithmetic,
but every component is a sample-based estimate carrying its own sampling error,
and the text says so wherever a combined figure is reported without a margin of
error.

## Foreign-born is a citizenship category, not a birthplace

The Census Bureau's glossary, archived in
`census_explorer/reference/census_definitions.json` with the source URL and the
document's checksum:

> The foreign-born population is composed of anyone who is not a U.S. citizen at
> birth. This includes persons who have become U.S. citizens through
> naturalization.

> the native-born population, which comprises anyone who is a U.S. citizen at
> birth, including people born in the United States, Puerto Rico, a U.S. Island
> Area (...), or abroad to a U.S. citizen parent or parents.

So "born outside the United States" names a different population: someone born
abroad to a U.S. citizen parent, or in Puerto Rico, is native-born. B05002
publishes exactly that category — "Native; born outside the United States" — so
the distinction is visible in our own data.

Every nativity measure is therefore labelled "foreign-born" and explained as
"not a U.S. citizen at birth, including those who have since naturalised". The
definitions are extracted from the archived glossary by pattern, and the
extractor raises rather than guessing if the wording changes.

## A percentage names its own denominator

The unit of a share is its denominator. Describing every percentage as "percent
of residents" is wrong for a share of the foreign-born, or of adults aged 25 and
over, because those are different populations. The interface and the brief both
take the unit from the measure's own universe.

A margin of error on a percentage is a span of **percentage points**, not a
percentage. Writing "±0.5%" invites reading it as half a percent *of the
estimate*, which is a different and much smaller number, so it is written
"±0.5 points".

## Controlled totals say so, and only when they are

An estimate controlled to an independent population total has no sampling
error. Printing "±0" reads as a measurement of remarkable precision rather than
the absence of one, so the table, the chart tooltip, the benchmark row and the
brief all say "none" with the reason. This applies to individual areas as well
as to combined references.

**That claim is recorded, never inferred.** A margin of error that could not be
computed is *unknown*; one that is zero because every contributing estimate is
controlled is *absent*. Describing the first as the second understates
uncertainty, and `None` is not zero.

`measures.compute` records an explicit `controlled` flag, carried into storage
as `ctl`, and sets it only when all of the following hold:

- a margin of error was actually computed, so its status is `ok`;
- every cell that contributed to it was controlled;
- no contributing cell had a margin of error that could not be computed.

For a share, both sides must qualify. A controlled denominator with a sampled
numerator still carries the numerator's sampling error, so the share is not
controlled — and a controlled denominator with a numerator whose margin of
error is missing yields an *unknown* margin of error, not an absent one.

Consumers read that flag. None of them looks for the word "controlled" in a
source flag: flags are pooled from the numerator and the denominator, so one
cell's flag says nothing about the result. A test asserts that no consumer
reintroduces the inference.

A published margin of error of zero that carries no controlled provenance is
reported as published — "± 0" — rather than dressed up as an absence of
sampling error. And a coefficient of variation is suppressed for a controlled
count: it is zero by construction, and "CV 0%" reads as a measurement.

## A brief describes itself, not its question

Three sentences in an exported brief used to be true of the tool rather than of
the file in front of the reader, and a brief is read by someone who was not
there when it was made.

- **What it shows** is derived from the selection: the measure actually
  included, the places actually covered, the period, and the reference if one
  was built. A question's own description says what that *question* can answer,
  which is not the same thing: a single-measure brief must not claim four.
- **What it can verify** depends on how it was produced. A brief exported from
  a saved project names that project and says the app checks its pinned digests
  on reopening. A brief opened straight from the interface says it is a
  generated snapshot whose digests record what it was computed from but which
  the HTML file itself cannot check. Neither claims to be an archive.
- **The quality copy** follows the selection. One area with no reference is not
  a comparison, and saying "places are compared with each other" there would be
  the brief's own first inaccuracy.

## Published universe and measure denominator are different things

A table's published universe is a property of the source table. A measure's
denominator is a property of the measure. For `naturalized_share_of_foreign_born`
the source table B05002 has the published universe "Total population" while the
share is computed out of the foreign-born population, so printing only
"Universe: Total population" beside a naturalisation share reads as a
contradiction.

Captions name both, labelled: "Published source table universe: … Measure
denominator: …". A count says its denominator is not applicable rather than
leaving the reader to infer it. The technical metadata — table codes, cell
codes, the published universe string — is unchanged and still travels in the
source panel, the CSV and the provenance document.

## Provenance

Every retrieved artifact has a manifest record: provider, kind, sanitized
source URL, sanitized request, retrieval timestamp, HTTP status, byte length,
SHA-256, and cache path. `verify manifests` re-hashes every artifact and
reports checksum, size and existence failures. A saved project pins the
manifest identifiers it was built from, so refreshing the cache cannot silently
change an older figure.

Every export bundle carries `data.csv`, `provenance.json` and `figure.svg` with
a `README.txt` explaining how to read the columns. The provenance document
lists the selection that produced the bundle and the digest of every file it
was computed from — and only those files. Sweeping in every manifest in the
directory, as an earlier version did, attached retrievals the export never read
and made the bundle look better attested than it was.

## Credentials

The key is read from the environment at the moment of a call. It is never
written to a cache file, a manifest, an export, a log line or an error message,
and the URL recorded in provenance has the credential parameter removed
entirely, not masked. The local service never reads a credential at all, so the
browser cannot receive one. Tests assert this against every endpoint and both
export files.
