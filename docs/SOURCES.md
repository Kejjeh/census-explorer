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
