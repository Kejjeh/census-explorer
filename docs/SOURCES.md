# Sources and claim review

Official documentation reviewed on 2026-09-20. These references establish planning constraints; they do not validate any historical counts from the supplied narrative. Future pipelines must archive the actual metadata/codebooks used.

| Reference | Relevance |
| --- | --- |
| [Census API key announcement](https://www.census.gov/library/video/2026/adrm/requesting-a-census-data-api-key.html) | Data queries now require an API key. |
| [Census June 2026 newsletter](https://content.govdelivery.com/accounts/USCENSUS/bulletins/41aaf23) | Clarifies that the key requirement was removed for metadata queries. |
| [2023 ACS five-year tables](https://api.census.gov/data/2023/acs/acs5/groups.html) | Official variable catalog; table availability and labels must be read per release. |
| [B06009](https://api.census.gov/data/2023/acs/acs5/groups/B06009.html) | Place of birth by educational attainment; does not identify NYC birthplace or recent arrival. |
| [B06004B](https://api.census.gov/data/2023/acs/acs5/groups/B06004B.html) | Place of birth for Black or African American alone population; not a detailed Caribbean birthplace cross-tab. |
| [2020 ACS experimental release](https://www.census.gov/programs-surveys/acs/data/experimental-data.html) | Experimental estimates replaced standard 2020 one-year estimates. |
| [ACS MOE guidance](https://www.census.gov/content/dam/Census/programs-surveys/acs/guidance/training-presentations/20170419_MOE.pdf) | 90% MOE and standard-error relationship; use documented methods for derived statistics. |
| [IPUMS sample descriptions](https://usa.ipums.org/usa/sampdesc.shtml) | Verify exact sample design and availability rather than assuming uniform 1% samples. |
| [IPUMS geographic tools](https://usa.ipums.org/usa/volii/tgeotools.shtml) | Geography and crosswalk support vary by sample and vintage. |
| [IPUMS ENUMDIST](https://usa.ipums.org/usa-action/variables/ENUMDIST) | ED availability and uniqueness require more than an unqualified ED number; early years require supervisor/state context. |
| [IPUMS historical strata](https://usa.ipums.org/usa/complex_survey_vars/strata_historical.shtml) | Historical survey variance needs design information; weights alone do not justify a CV. |
| [About NHGIS](https://www.nhgis.org/about-ipums-nhgis) | Historical aggregate statistics and GIS boundaries are a separate useful source, extending back to 1790. |
| [NHGIS availability](https://www.nhgis.org/data-availability) | Verify geography/year coverage before promising historical maps. |

## Source strategy

Use Census published aggregate tables for the initial modern slice, IPUMS USA for harmonized person/household microdata questions, and assess NHGIS for historical aggregates and map boundaries. The claim that all historical census work must come from IPUMS microdata is too broad: aggregate and geographic sources can answer different parts of this project.

The Census key requirement is documented; this setup did not attempt an authenticated data query. No IPUMS sample IDs, group codes, API submission syntax, historical count benchmarks, Urban Transition downloads, or 2026 NYC publication claims have yet been verified.

## Sources used by the implemented pipeline

Added when stages A-C were implemented. Each is retrieved by an explicit
command, cached with a checksum, and recorded in a manifest.

| Reference | Used for |
| --- | --- |
| [2023 ACS 5-year group metadata](https://api.census.gov/data/2023/acs/acs5/groups/B05006.json) | Exact estimate/MOE/annotation variable names, labels, concepts and universes. Open; no key required. |
| [2022 ACS 5-year group metadata](https://api.census.gov/data/2022/acs/acs5/groups/B05006.json) | The same, for the comparison release; also the basis of the variable-drift check. |
| [ACS table-based Summary File, 2023 5-year data](https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/5YRData/) | The keyless observation transport. One pipe-delimited file per table, covering every published geography. |
| [ACS table-based Summary File documentation](https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/documentation/) | Table shells and geography lists for the same release. |
| [Notes on ACS estimate and annotation values](https://www.census.gov/data/developers/data-sets/acs-1year/notes-on-acs-estimate-and-annotation-values.html) | The authoritative annotation table. Archived verbatim and machine-extracted into `census_explorer/reference/acs_annotation_values.json` with the document checksum. |
| [Cartographic boundary files, GENZ2023](https://www2.census.gov/geo/tiger/GENZ2023/shp/) | County and New York State tract polygons at 1:500,000, matching the 2023 ACS geography vintage. |
| [Cartographic boundary files, GENZ2022](https://www2.census.gov/geo/tiger/GENZ2022/shp/) | The same, for the comparison release. |
| [Census Data API key signup](https://api.census.gov/data/key_signup.html) | The optional keyed transport. Metadata queries do not need a key; data queries do. |

Hospital Explorer phase 1 adds CMS Hospital General Information and its
Footnote Crosswalk, the NYSDOH HFIS General and Certification datasets, the
NYS ITS Locality Hierarchy and the Open NY terms. Their releases, checks,
data contracts and known flaws are in `docs/HOSPITALS.md`.

### Observations recorded while implementing

- Keyed data queries redirect to a "Missing Key" page rather than returning an
  error body. The API client refuses to cache a response that is not a JSON
  array, so this cannot become a silently empty dataset.
- `B05006`'s published universe is *"Foreign-born population excluding
  population born at sea"*, which is narrower than `B05002`'s foreign-born
  count. The two totals are reported side by side as a diagnostic rather than
  reconciled.
- The published label for `B05002_013` changed between the 2022 and 2023
  releases (`Foreign-born:` to `Foreign born:`). The comparison check reports
  it; nothing suppresses it.
- `B05006`, `B06009` and `B06004B` are all published at census-tract level in
  the five-year Summary File. This was verified from the retrieved data, not
  assumed.
- Summing the five borough rows reproduces the separately published New York
  City place row (GEOID 3651000) exactly for all six reconciled cells, in both
  releases.

## Still unverified

Everything under "Claims awaiting verification" in `RESEARCH_PLAN.md` remains
unverified. No IPUMS sample, NHGIS table, historical count, birthplace group
code or 2026 NYC publication claim has been checked, and no historical data has
been retrieved.
