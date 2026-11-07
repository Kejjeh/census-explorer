# Research plan

## Product direction

Build a personal, local census explorer supporting variable discovery, map and table views, comparisons over time, and exportable figures with source and uncertainty metadata. NYC population change is the first research project. The two supplied narratives are design inputs, not verified results or an existing implementation.

## First chart contract

- One row per group and a shared scale, with direct labels and each row's observed peak value and period.
- Primary denominator: the population of the documented five-borough footprint in the same reference period. For pre-consolidation dates, reconstruct that footprint from documented geographic components; do not substitute historical New York City's administrative limits.
- Companion denominator: foreign-born population in the same period and geography, only for compatible foreign-born numerators. Puerto Rican birthplace and other U.S.-territory cases require an explicit nativity rule.
- Blue for birthplace-defined groups; gray for race/education-defined US-born cohorts. Add text labels so color is not the only distinction. Groups may overlap and are not a partition of the city.
- Historical decennial observations omit 1890. A 2019-2023 ACS period estimate must retain that label and must not be silently represented as a 2020 census count. Document overlap if shown alongside a 2020 observation.
- No smoothed or interpolated values without visible disclosure. Show unavailable observations as gaps.
- Digitized counts with alleged +/-10-15k eyeballing error are illustrative only. This range is not a sampling margin of error. The chart itself and its numerical dataset have not been supplied.

## Eight research directions

**None of these is complete.** No historical microdata has been retrieved and
no evidence gate below has been met. The table is the plan, not a report.


| Direction | Target | Evidence and acceptance gate |
| --- | --- | --- |
| 1. Generations | Foreign-born and US-born children of immigrant parents | Audit parental-birthplace availability for every sample. Define mixed-parentage allocation without double counting. Do not assume availability through 1970 is universal. |
| 2. Settlement maps | Borough, PUMA, and historical neighborhood views | Establish geography availability by sample; verify ED identifiers and boundary sources, vintages, join keys, and unmatched population. CITY alone is not a five-borough historical crosswalk. |
| 3. Arrivals and exits | Prior-residence flows into and out of NYC | A NY-resident-only extract misses leavers. Define a national origin/destination extract and validate one-year versus five-year lookback availability and origin geography. Never infer an outflow from a falling stock. |
| 4. Policy context | Markers for 1924, 1965, 1986, 1991, 2020 | Source descriptions and effective dates before rendering. These are contextual events, not estimated causal effects. |
| 5. Foreign-born composition | Each compatible group's share of all foreign-born | Reconcile both numerator and denominator; establish stable birthplace group definitions across changing borders and categories. |
| 6. Age structure | Weighted median age and age pyramids | Verify person weights, age top codes and missingness, small samples, and design-appropriate uncertainty. Do not infer future decline from median age alone. |
| 7. Economic sorting | Education, occupation, earnings, ownership and rent burden | State age/employment/household universes, dollar bases, top codes, occupation harmonization, and person versus household estimands. Wage income is not total income; ownership and rent burden need household rules. |
| 8. Expanded roster | Poland, Austria-Hungary, Jamaica, Mexico, Guyana, Ecuador, Bangladesh, Caribbean-born Black residents | Verify BPL/BPLD codes per sample. Historical Austria-Hungary needs a territorial rule. Caribbean-born Black is an intersection and may overlap country groups. |

The original 23-group roster, three mover definitions, and eight-idea processing code were not supplied. Do not reconstruct exact codes from memory.

## Measurement boundaries

B05006 is a candidate for foreign-born birthplace counts. B06009 measures birthplace by education; B06004B measures birthplace for the Black-alone population. These tables do not directly measure recent arrivals, NYC-born residents, or the detailed cross-tabulation needed for Caribbean-born Black residents. Define any label such as 'college-degree transplant' precisely, including age universe, education cutoff, birthplace rule, and whether it means a stock or a flow.

Keep ACS one-year and five-year products separate. Standard 2020 ACS one-year estimates were not released; do not fill that gap using experimental estimates without distinct treatment. Adjacent five-year windows overlap and are not independent observations. PUMA identifiers and boundaries also change; preserve vintages before comparing.

ACS published margins of error are typically at 90% confidence. Preserve original MOEs and annotations. SE = MOE / 1.645 is applicable only to valid numeric MOEs. A CV requires a valid SE and a nonzero estimate; negative sentinel codes, suppression and nulls are not data values. Ratios, sums, differences, medians and microdata estimates need their own documented variance procedures. Never manufacture a CV for every historical cell.

Historical samples differ in coverage, sampling fractions, available variables, and geography. Audit coverage of enslaved people and other excluded populations when defining nineteenth-century denominators. Do not assume all 1950-1980 samples are 1%, or that each decennial year has the same geography. Distinguish actual sample size from weighted population.

## Staged delivery

1. **Foundation (done):** repository, scope, source review, provisional configuration and research rules.
2. **Modern data slice (done):** metadata and original responses are cached for two ACS five-year releases across the five boroughs and 2,327 census tracts, with estimate/MOE pairing, annotation handling and universes preserved, sanitized requests, retrieval timestamps, response hashes and code revisions recorded, and six cells per release reconciled exactly against the separately published New York City row. Offline tests cover missing values, sentinel handling, duplicate geography, malformed responses, credential redaction and cache replay. See `USAGE.md`.
3. **First explorer (done):** a local browser application with a searchable catalog, linked map and sortable table, an area inspector showing margins of error, flags, definitions and sources, saved project definitions, and CSV + provenance + figure exports. Maps use matching-vintage Census boundaries with a reported join. No third-party dependency was added; see `DEPENDENCIES.md` for the DuckDB/MapLibre decision, which is still open.
4. **Historical feasibility audit (not started):** explicit sample IDs and fractions, variable availability matrix, verified group-code crosswalk, historical footprint reconstruction and variance plan. Start with a small extract. No arbitrary multi-gigabyte extraction before feasibility is established.
5. **Historical series and generations (not started):** reconcile benchmark years; make gaps and definitional breaks visible. Add household-design and weight checks before publishing uncertainty.
6. **Spatial and migration extensions (not started):** verified boundary joins, separate national migration extract, documented origin/destination resolution, age and economic comparisons.

## Reproducibility contract

Each result needs source, dataset/sample, period, geography ID/vintage, universe, measure, group-definition version, weight basis, estimate, uncertainty method/status, and input manifest reference. Microdata outputs additionally preserve unweighted cell size when disclosure terms allow. Store codebooks and extract definitions with provenance. Raw personal records stay local and outside git.

## Claims awaiting verification

- Irish 1860 peak relative to subsequent groups; first-plus-second-generation Irish/German shares around 1900; top-two foreign-born concentration in 1860 and today.
- Digitized Italy, Dominican Republic and China counts and claimed corrections.
- The claimed age of Italy-born residents and the named neighborhood succession paths.
- Uniform ED coverage in 1880-1940, borough identification by sample, and five-year migration availability 'from 1940 on'.
- A publication titled the 2026 *Newest New Yorkers*, its vintage and claimed borough/neighborhood tables.
- Extract size, sample count, request duration, and code execution claims from the original notes.

None of these claims should appear as a research finding until its source, geography, universe, and period have been checked.
