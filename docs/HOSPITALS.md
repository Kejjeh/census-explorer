# Hospital Explorer, phase 1

Status: **built and checked locally, not published.** The static site still
serves the census-only publication (`gh-pages` `eb96a10`). No CMS or NYS
hospital data has been deployed. This phase stops here for independent review.

The phase adds an optional hospital layer and directory to the explorer:
- **HFIS sites.** Hospitals and their extension sites, as licensed by the
  New York State Department of Health (NYSDOH) and published in its Health
  Facilities Information System (HFIS). Keyed by the HFIS site identifier,
  `fac_id`.
- **CMS reporting entities.** New York's Medicare reporting entities, from
  the Centers for Medicare & Medicaid Services (CMS). Keyed by the CMS
  Certification Number (CCN).

The two are kept apart throughout.

## Sources and the releases used

All sources are keyless and public. They are retrieved only by
`python -m census_explorer.cli hospitals fetch`.

| Key | Dataset | Publisher | Release used | Rows |
| --- | --- | --- | --- | --- |
| `cms_general` | [Hospital General Information](https://data.cms.gov/provider-data/dataset/xubh-q36u) (`xubh-q36u`) and its [data dictionary](https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/hospital/HOSPITAL_Data_Dictionary.pdf) | CMS | released 2026-08-13, modified 2026-07-22 | 5,419, of which 192 are in NY |
| `cms_footnotes` | [Footnote Crosswalk](https://data.cms.gov/provider-data/dataset/y9us-9xdf) (`y9us-9xdf`) | CMS | released 2026-08-13, modified 2025-09-18 | 32 |
| `nys_general` | [Health Facility General Information](https://health.data.ny.gov/d/vn5v-hh5r) (`vn5v-hh5r`) | NYSDOH | rows updated 2026-09-01T12:21:17Z | 5,992 |
| `nys_certification` | [Health Facility Certification Information](https://health.data.ny.gov/d/2g9y-7kqm) (`2g9y-7kqm`) | NYSDOH | rows updated 2026-09-01T12:18:03Z | 45,371 |
| `locality` | [NYS Locality Hierarchy with Websites](https://data.ny.gov/d/55k6-h6qq) (`55k6-h6qq`) | NYS ITS | rows updated 2023-03-06 | 1,605 |
| `open_ny_terms` | [Open NY Terms of Use](https://data.ny.gov/download/77gx-ii52/application/pdf) (PDF) | New York State | as retrieved | — |

The retrieval used here is `hospitals-20260925T174343+0000`. Its 12 artifacts
are under `data/raw/hospitals/20260925T174343+0000/`, which is git-ignored and
never overwritten. `verify manifests` re-hashes them.

**Terms.**
- The NYS datasets declare no license. Their Socrata metadata names NYSDOH, or
  NYS ITS for the Locality Hierarchy, as the attribution, and the Open NY
  terms are archived with the retrieval.
- The CMS metadata declares `accessLevel: public` and has no license field.
  No dataset-specific CMS terms page was found.
- The interface credits each source and states its date.

## What every retrieval checks

Retrieval stops, and keeps nothing, if any check fails. Files are staged in a
`.partial` directory and renamed only once all checks pass.

- **Schema drift.** The CSV header must equal the header in
  `config/hospitals.json` exactly, in the same order.
- **Truncation.**
  - The number of rows in each complete CSV export must equal the provider's
    count API.
  - The NY subset is also counted by the provider and must match the local
    count: `state = NY` in the CMS datastore (192 = 192), and the
    hospital-family types in Socrata (1,743 = 1,743).
- **Safe addresses.** The CMS download addresses come from the CMS metadata
  and must be https on `data.cms.gov`. Documents must begin with `%PDF`.

## Data contracts

- **Identifiers.**
  - The HFIS site ID (`fac_id`, "Site specific facility identification
    number") and the CCN are both kept as text.
  - CCNs are not all numeric: VA hospitals have CCNs such as `33009F`.
  - No CCN is assumed to equal a `fac_id`.
- **One site per `fac_id`.**
  - HFIS General repeats a site once per operator or cooperator: 164 IDs
    appear on more than one row.
  - The 1,743 hospital-family rows describe **1,579 sites**. Site-level
    fields agree across repeats: 0 conflicts.
  - Operators, cooperators and ownership types are kept as lists. A site
    with more than one ownership type is filtered under "Listed with more
    than one ownership type" (none in this release).
- **Hospital family.** The dataset's own metadata says it holds hospitals and
  extension clinics, but it holds 23 facility types.
  - **Hospitals** (218): Hospital, Critical Access Hospital, and Rural
    Emergency Hospital. 69 of them list another hospital as their main site;
    these are divisions.
  - **Extension sites** (1,361): hospital, school-based, mobile and CAH/REH
    extension clinics, and off-campus emergency departments.
  - **Excluded:** the other 13 types (4,249 rows), counted by type in the
    coverage report.
- **Main-site links.** Each link is shown as HFIS lists it, never inferred.
  All 1,361 extension sites name a main site in the registry.
- **Certification rows.**
  - Kept row for row in `certification.json`, keyed by `fac_id`, and never
    joined onto site rows. 10,470 rows belong to registry sites.
  - Beds: 1,136 rows are "Bed / Permanent". Each certified bed count is
    shown by category and never added up. They are certified beds, not
    staffed, available or occupied beds.
  - Services: the measure value of the 7,142 service rows with a blank sub
    type is 0. That contradicts the dictionary's "Count of bed or service
    unit", so service measures are not shown as counts.
  - Dates: effective dates of 2008-12-30 (1,921 site rows) are flagged. NYSDOH
    documents that date as the system-conversion default, so the true date
    is unavailable.
  - Of the rest: 34,897 rows belong to non-hospital facilities. 4 rows name
    IDs that are absent from HFIS General (`399`, `4791`); they are reported,
    not dropped silently.
- **Counties.** HFIS county codes are NYSDOH gazetteer codes (for example New
  York = 7093), not FIPS.
  - FIPS come from the Census Bureau's own county list: `NAME` and `GEOID`
    from the GENZ2023 county file. That file is checked against its ACS
    geography manifest before use.
  - One reviewed alias applies: HFIS "Saint Lawrence" = Census "St. Lawrence".
    All 61 HFIS county codes match; Hamilton County has no site.
  - The Locality Hierarchy is a cross-check only. **It gives St Lawrence the
    FIPS 36099, which is Seneca County's code** (St Lawrence is 36089). This
    is reported, and that code is never used. Using the Locality Hierarchy
    alone would have put every St Lawrence site in Seneca.
- **Locations.**
  - Only the published coordinates are used. NYSDOH says they are
    "Geo-coded to mailing address".
  - 1,565 sites are located. 14 have no coordinates; they stay in the
    directory and the CSV with the reason.
  - Each located point is checked against the Census county shapes:
    - 1,517 fall in the listed county;
    - 48 fall in another county, mostly near a county line. These are
      reported and drawn as published.
- **CCN → HFIS candidates.** No official crosswalk was found. The only NYS
  dataset with Medicare numbers covers nursing homes.
  - One documented rule generates candidates: exact equality of the
    normalized street address and the five-digit ZIP. Normalization is:
    upper case, punctuation removed, and a listed set of street-word
    abbreviations.
  - Name-word overlap is shown next to each candidate as a review aid only;
    it plays no part in the rule.
  - Results for the 192 NY CCNs:
    - **126 candidate**: exactly one HFIS hospital, which no other CCN
      shares.
    - **2 ambiguous**: 330166 and 330801 both point to HFIS 111.
    - **64 unresolved**: 9 VA, 1 DoD, 28 psychiatric (OMH-licensed hospitals
      are not in HFIS), 3 critical access, 1 rural emergency and 22
      acute-care hospitals. Examples: an address
      with a mail code, an intersection, a PO box.
  - No CCN is ever called a match. No rating is copied to a site.
- **CMS ratings.**
  - Shown only on the CMS entity, as published: 136 rated, 56 not available.
  - "Not available" always carries the CMS footnote text: 36 footnote 19,
    17 footnote 16, 2 footnote 5, 1 footnote 22.
  - There are no composites, averages, rankings, recommendations or access
    scores. No census margin of error is applied to CMS data.
- **Dates.** CMS, NYSDOH and ACS dates are stated separately on the page.
  Nothing attributes population to any site or tract.

## Commands

```bash
python -m census_explorer.cli hospitals fetch      # LIVE, explicit; keyless
python -m census_explorer.cli hospitals build      # offline: verifies the manifest, writes data/processed/hospitals/
python -m census_explorer.cli hospitals report     # offline: prints counts, checks and reports
python -m census_explorer.cli verify manifests     # offline: re-hashes every cached artifact
python -m census_explorer.cli serve                # the layer is under the map; off by default
python -m census_explorer.cli site build --base /census-explorer/ \
  --data-dir data/release-acs5_2023 --hospitals data/processed/hospitals --out <dir>
```

`site build` without `--hospitals` is exactly the census-only build.

**Building with `--hospitals`:**
- It adds `hospitals.js`, `data/hospitals/registry.json` and
  `data/hospitals/certification.json`.
- Both data files are digested, and the page checks them before use.
- They are not part of the census snapshot. A build with them keeps the
  published snapshot `ddac376b…`, and every census data file stays
  byte-identical to the published candidate. The files that differ are
  `app.js`, `app.css`, `static.js`, `index.html`, `data/manifest.json` and
  `data/digests.json`.

## Evidence

**Offline, on synthetic fixtures.** No row in these tests describes a real
hospital.
- `python -B -m unittest tests.test_hospitals`: 30 tests, OK. They cover:
  - IDs with leading zeros and letters;
  - repeated rows becoming one site;
  - certification rows kept without fan-out;
  - beds never totalled;
  - the conversion-date flag;
  - invalid coordinates;
  - county FIPS from Census, and the reproduced Locality flaw;
  - candidate, ambiguous and unresolved CCNs, with no rating copied to a
    site;
  - footnotes, including an unknown code;
  - truncated exports, schema drift, reordered columns, NY- and
    family-count mismatches, and an unsafe download address;
  - a malformed CSV, a tampered cache and a tampered county file;
  - a duplicate CCN;
  - the local route;
  - the static build: snapshot stability and byte-identical census files;
  - mismatched registry files.
- `node --test tests/js/hospitals.test.js`: 8 tests. They cover:
  - filters, and map/table/CSV parity;
  - CSV formula guarding;
  - unavailable ratings;
  - links limited to official https hosts;
  - escaping.
- Full suite: `python -B -m unittest discover -s tests -t .`, 508 tests, OK,
  3 skipped.

**Live, on the real retrieval**, registry built at `8b135ce`, headless
Chromium. The command is
`node tests/acceptance/journeys.mjs --local http://127.0.0.1:8765/ --static
http://127.0.0.1:8899/census-explorer/`. All journeys passed: 209 checks, 0
failed, in both local and static modes. The hospital journeys covered:
- the layer is off by default, and nothing hospital-related is requested
  before it is turned on;
- the registry loads with every reconciliation check passed;
- a county filter (Rockland, 9 sites, 1 unlocated) draws exactly its 8
  located sites, and the summary and table agree;
- the CSV holds exactly the 9 filtered records;
- an unlocated site shows its reason and has no marker;
- an extension site links to its main site, and back;
- no rating appears on an HFIS site;
- a marker click opens a site without changing the census selection;
- CMS "Not available" shows footnote 16's text, and a rated entity shows
  "n of 5" with its Care Compare link;
- the ambiguous CCN is labelled ambiguous;
- turning the layer off restores the census map, table, legend, scope and
  address exactly;
- at 390 CSS px, keyboard only: toggle, search, filter, open a row
  (focus moves to the details), beds by category, and the CSV by Enter,
  with no horizontal overflow and focus never on the page body;
- the static copy made no request outside its origin.

## Known gaps

- **NYS Health Profiles links.** The profile site uses its own numbers, such
  as `/hospital/view/102911`, not `fac_id`. No page on it shows a PFI. The
  site links to the official A–Z list, not to each hospital's profile. A
  per-site link needs a reviewed crosswalk.
- **Care Compare links.** Each link is built from the CCN in CMS's own
  format. The site answers 200 for any identifier, so a link is not proof
  that a profile exists.
- **CCN candidates are unreviewed.**
  - The exact address rule is conservative: 22 acute-care CCNs are
    unresolved because of address wording.
  - A CCN can cover several campuses, and the rule finds at most the
    campuses at its own address.
  - A reviewed decision file would be the next step.
- **Hospital state is not in share links**, and share links do not restore
  the layer. The census share link is unaffected.
- **Published open dates are not shown.** Many read 01/01/1901. They are in
  the CSV as published.
- **Not covered:** NYS Office of Mental Health licensed psychiatric hospitals
  and federal hospitals, beyond their CMS rows. Also not covered: SPARCS,
  cost reports, historical snapshots, other states, staffed or available
  beds, travel time and service areas.
- **Browsers:** Chromium only. There was no screen-reader pass and no
  physical device.
