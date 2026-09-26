# Hospital Explorer, phase 1

Status: **built and checked locally, not published.** The static site still
serves the census-only publication (`gh-pages` `eb96a10`). No CMS or NYS
hospital data has been deployed. A reviewable release candidate is described
under "Release candidate"; publication waits for independent acceptance.

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

The retrieval used here is `hospitals-20260925T212532+0000`. Its 12 artifacts
are under `data/raw/hospitals/20260925T212532+0000/`, which is git-ignored and
never overwritten. `verify manifests` re-hashes them.
- The first retrieval, `hospitals-20260925T174343+0000`, is kept, but builds
  refuse it: it predates the pinning of the retrieval configuration (see
  below), and the configuration was edited after it.
- Its data files are byte-identical to the new retrieval's, except the
  Locality Hierarchy. That export holds the same 1,605 rows, in a different
  order: the Socrata export order is not stable. Nothing built depends on
  row order.
- Registry counts are unchanged between the two retrievals.

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

## Provenance of every build

- **Retrieval configuration.** `config/hospitals.json` holds only what
  retrieval checks: sources, expected headers, documents, and the
  hospital-family types the provider counts. Its SHA-256 is pinned in each
  manifest. That hash is taken over canonical JSON, so line endings and key
  order do not change it. A build refuses a retrieval whose configuration has
  changed since, or that records none, and says how to retrieve again.
- **Transformation rules.** County aliases, NY bounds, the certification note
  and links are in `config/hospital_rules.json`, with a `rules_version`. The
  canonical SHA-256 of the rules (`rules_sha256`) is recorded in:
  - `registry.json`, `certification.json` and `index.json`;
  - the published `data/manifest.json`;
  - every CSV row.

  A changed rule therefore always shows up as a different `rules_sha256`
  under the same retrieval. The build here is rules v1, `ddad50c2…`.
- **Paired outputs.** `hospitals build` writes `index.json`, holding the
  SHA-256 of `registry.json` and `certification.json` and their shared
  retrieval, rules, mode and schema.
  - `site build`, the local service and `hospitals report` refuse any file
    that does not match the index, or any pair that disagrees on these.
  - The page refuses a certification file that does not belong to the
    registry it loaded.
- **Data mode.** Only the real network fetcher produces a `live`
  retrieval. An injected fetcher, such as the tests' fake, always produces
  `fixture`.
  - Unknown modes are refused.
  - `site build` refuses hospital data whose mode differs from the census
    build's, so live census figures are never published beside synthetic
    hospital records.
  - For any non-live registry, the page shows a SYNTHETIC HOSPITAL DATA
    banner, heading and map note, and names the CSV `hospital-sites-SYNTHETIC-…`
    with a `data_mode` column.

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
  - **Conflicts fail closed.** If repeated rows disagree on any site-level
    field, the build stops and names each fac_id, field and value. No value
    is chosen by row order. Site-level fields are: name, type, address,
    city, ZIP, county code and name, coordinates, main site, operating
    certificate, phone, regional office and open date.
  - Operators, cooperators and ownership types are kept as sorted lists. A site
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
  - Beds: each Bed row is kept whole on its site (`certified_beds`) and in
    the CSV (`certified_bed_records_json`, one CSV row per site). Each record
    holds the category, the count or `null` with a note, the raw value, the
    sub type, the raw and parsed effective date, and a date note. Records
    are never added up. They are certified beds, not staffed, available or
    occupied beds, and not current capacity.
  - In this release, all 1,136 bed rows are "Permanent" with whole counts,
    no site repeats a category, and 19 bed rows carry the conversion date.
    The other cases are exercised only by the fixture tests: the same
    category with another sub type or date, a missing or non-numeric count,
    and an unparseable date.
  - Services: the measure value of the 7,142 service rows with a blank sub
    type is 0. That contradicts the dictionary's "Count of bed or service
    unit", so service measures are not shown as counts.
  - Dates: effective dates of 2008-12-30 are flagged: 1,921 certification
    rows for registry sites, beds and services together. NYSDOH
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
    - 48 fall in another county. For example, HFIS 15716 (SJEH Mobile
      Health Van) is listed in Albany; its address is in Far Rockaway, and
      its published point lies in Queens.
- **Map contract** (`map_status`, registry schema 2).
  - **County filters** use the county HFIS lists, as published. It is never
    relabelled, even when the address or point suggests another county.
  - **The map** draws a site only when its published point lies inside
    that listed county (`mapped`, 1,517 sites).
  - **Sites not drawn** stay in the directory and the CSV with a
    controlled status and a sentence:
    - `not_mapped_county_conflict` (48): the point lies in another county.
      The table says, for example, "Not mapped: published point is in Queens
      County, not the listed Albany". The details name both counties and the
      FIPS code. The CSV carries `hfis_county_name`, `county_fips`,
      `point_county_fips`, `point_county_name`, `map_status`, `map_reason`
      and the published coordinates.
    - `not_mapped_no_location` (14): no usable coordinates.
    - `not_mapped_county_unverified`: the point is not in exactly one
      county shape (none in this release).
  - The directory summary and the map note count four groups separately:
    drawn, outside the mapped area, point outside the listed county, and
    no location. Both say that points are NYSDOH geocodes of each site's
    mailing address.
  - The coverage report lists all 48 sites with both counties.
  - **Why not draw them?** Drawing a conflict site under a county filter
    puts a mark somewhere the filter says it is not. Filtering it by the
    point's county would silently relabel the source. Neither value is
    corrected, and nothing is geocoded.
  - Statewide, the journey checks that the markers are exactly the 1,517
    `mapped` sites.
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
  - An extension site that shares a CCN's address is evidence only. It is
    marked `role: same_address_extension_site`, and the page shows "same
    address only; not a candidate". Only an HFIS hospital can be a
    candidate. (15716 shares its address with CCN 330395, whose candidate
    is HFIS 1635.)
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

## Release candidate (not published)

This candidate is built at code `3b8d965` against the pinned census release
that produced the published snapshot. Nothing was pushed to `gh-pages`.

| | |
| --- | --- |
| Census input | `data/release-acs5_2023/` in the implementer's environment. This is the processed 2019-2023 ACS release built at `efc9ea1` from `observations-summary-file-acs5_2023-20260924T033904+0000` (with the metadata, geography, roster and reconciliation manifests listed in its `dataset.json`). `dataset.json` SHA-256 is `7eae2f74…`. All 53 input files are listed in `docs/acs5_2023-release-ddac376b-inputs.sha256`. |
| Hospital input | Retrieval `hospitals-20260925T212532+0000`, rules v1 `ddad50c2…`, registry built at `3b8d965`. `index.json`: `registry.json` `c09c8373…`, `certification.json` `7fc3fd5b…`, schema 2, live. |
| Command | `python -m census_explorer.cli site build --base /census-explorer/ --data-dir data/release-acs5_2023 --hospitals data/processed/hospitals --out artifacts/hospital-candidate-3b8d965/census-explorer` |
| Candidate | 116 files. `data/digests.json` `ecb4971b…`. All 114 digests and all 107 in-page digests match. Inventory equals files. `FILES.sha256` `1a38254e…` (copied to `docs/hospital-candidate-3b8d965.sha256`). Tarball `03eb6972…` (3.5 MB). |
| Snapshot | `ddac376b20c21661b0b92e02bd82435de7851f3d8a5112b50e7b9bb66fb7f60c`, the published one. |
| Provenance in `data/manifest.json` | `code_revision` `efc9ea1`: the census data build's revision, unchanged, and also in `status.json` and the page footer. `site_code_revision` `3b8d965`: new, the code that wrote the site. `hospitals.code_revision` `3b8d965`. |

**Continuity with the publication.** Compared file by file against
`origin/gh-pages` `eb96a10`, extracted with `git archive`:
- **107 of the 113 published files are byte-identical**, including **all 104
  census data files** (every file under `data/` except `data/manifest.json`
  and `data/digests.json`).
- 6 files differ: `app.css`, `app.js`, `static.js`, `index.html`,
  `data/manifest.json` and `data/digests.json`.
  - `data/manifest.json` differs only in `generated_at`, `site_code_revision`
    and the new `hospitals` section.
  - `index.html` carries the same snapshot. Its in-page digests add the two
    hospital files.
- 3 files are new: `hospitals.js`, `data/hospitals/registry.json` and
  `data/hospitals/certification.json`.
- The census-only build from the same input and code gives the same result
  without the two hospital data files.
- Share links made on the publication therefore open on the candidate.

**What another checkout can and cannot reproduce.**
- The census release is identified by its retrieval manifests and
  `dataset.json`. A checkout that built its census data from another
  retrieval has different `dataset.json` and `status.json` bytes, and so a
  different snapshot. The reviewer's checkout, for example, gives
  `bb4b31d6…`.
- Its estimates can still be identical: the publication before this one
  matched 103 of 113 files across the two retrievals.
- To check census continuity without the implementer's processed release,
  compare the census data entries of `docs/hospital-candidate-3b8d965.sha256`
  against the published files. Each `census-explorer/data/…` line, other
  than `manifest.json`, `digests.json` and `data/hospitals/`, should equal
  the SHA-256 of the same path on `gh-pages` `eb96a10`.
- Reproducing the candidate byte for byte needs `data/release-acs5_2023/`.
  Its 53 file hashes are in `docs/acs5_2023-release-ddac376b-inputs.sha256`,
  but the files are git-ignored. They can be handed over through a channel
  the reviewer authorizes.

## Evidence

**Offline, on synthetic fixtures.** No row in these tests describes a real
hospital. These are the implementer's runs at `3b8d965`.
- `python -B -m unittest tests.test_hospitals`: 45 tests, OK.
  - This round added the reviewed pattern: a fixture site listed in Albany,
    with its published point in another county, sharing a CMS entity's
    address. The tests check that the source county and coordinates are
    kept, the map status and reason, the coverage list, the per-site map
    statuses, and that the extension site is evidence, not a candidate.
  - The previous round added:
  - a conflicting address, in either row order, with both values in the
    error;
  - conflicting coordinates, county code, county name, main site and name;
  - a fully reversed input building an identical registry;
  - changed family types or sources refused, and an unpinned retrieval
    refused;
  - changed bounds and aliases changing the output and the recorded
    `rules_sha256`, and a rules hash that ignores line endings;
  - index round trip, an independently modified file, a mismatched pair,
    and unknown modes;
  - a tampered registry refused by `site build` and by the service;
  - live census with fixture hospitals refused by `site build`;
  - every bed record kept whole, including a second sub type and date, a
    missing count, a non-numeric count, an unparseable date and a
    conversion date.

  The earlier tests cover:
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
- `node --test tests/js/hospitals.test.js`: 13 tests.
  - New this round: a conflict site is never drawn under any map scope, the
    four plan counts always add up to the filtered list, and the table label,
    the summary sentence, and the CSV columns and coordinates are checked.
    An extension site's CCN is labelled "same address only".
  - Earlier: the bed-record JSON cell, the pairing check, the synthetic-data
    warning, and:
  - filters, and map/table/CSV parity;
  - CSV formula guarding;
  - unavailable ratings;
  - links limited to official https hosts;
  - escaping.
- Full suite: `python -B -m unittest discover -s tests -t .`, 523 tests, OK,
  3 skipped.

**Independent reviewer.** As reported by the reviewer, and separate from the
implementer's runs:
- **At `426e217`, Windows:**
  - full suite 521 tests, OK, 6 skipped; hospital JS 11/11;
  - a fresh keyless live retrieval `hospitals-20260926T011722+0000` and an
    offline registry build: all 11 reconciliation checks pass, 1,579 sites,
    192 NY CMS entities, 0 conflicts;
  - a local static build with 114/114 digested files matching;
  - census-only and hospital builds with identical census data bytes and
    the same snapshot, `bb4b31d6…`. This is not the published `ddac376b…`,
    because that checkout's census release comes from another retrieval.
  - The reviewer found the 15716 defect: listed in Albany, point in Queens,
    shown as "Mapped". The fix in this round has not yet been independently
    verified.
- **At `2fd0d2c`, Windows:** 508 tests, OK, 6 skipped.

**Live, on the real retrieval** `hospitals-20260925T212532+0000`, registry
built at `3b8d965`, headless Chromium. These are the implementer's runs. The
static target is **the release candidate itself**, served under
`/census-explorer/`. The command is `node tests/acceptance/journeys.mjs
--local http://127.0.0.1:8765/ --static http://127.0.0.1:8899/census-explorer/`.
All journeys passed: **221 checks, 0 failed**, local and static. The report
is kept with the candidate. New this round:
- In a county filter chosen to contain both an unlocated site and a conflict
  site (Oneida: 44 sites, 1 unlocated, 1 conflict), the map draws exactly
  the 42 sites whose point lies in Oneida. The summary states all four
  counts and the geocode wording.
- Statewide, the 1,517 markers are exactly the `mapped` sites.
- HFIS 15716 checks:
  - the table reads "Not mapped: published point is in Queens County, not
    the listed Albany";
  - the details give the published point and the reason;
  - the CSV row has `map_status` `not_mapped_county_conflict`, Albany
    36001, point county 36081 and the coordinates as published;
  - it is never drawn;
  - its CMS cell reads "330395 same address only; not a candidate";
  - the coverage report lists all 48 such sites.

Earlier checks, still passing:
- the registry is live data, with rules v1, and shows no synthetic-data
  banner;
- each CSV row's bed-record JSON equals the registry's records for that
  site, and no column is a total;
- the layer is off by default, and nothing hospital-related is requested
  before it is turned on;
- the registry loads with every reconciliation check passed;
- the CSV holds exactly the filtered records;
- an unlocated site shows "Not mapped: …" in the table, its reason in the
  details, and has no marker;
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

**Stubbed responses, local service, implementer.** The registry and
certification responses were rewritten to `fixture` in the browser, because
no live-mode build can carry fixture hospital data. The checks passed:
- banner, heading and map note say SYNTHETIC HOSPITAL DATA;
- the CSV is named `hospital-sites-SYNTHETIC-…` and has `data_mode` fixture;
- a certification file from another retrieval is refused, and no bed or
  service is shown.

The script is in the implementer's scratch space and is not part of the
repository harness.

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
- **Footer revision.** The page footer and `status.json` give the census
  data build's revision (`efc9ea1`), not the page code's. Changing that
  would change the snapshot. The page code's revision is
  `data/manifest.json` `site_code_revision`.
- **Browsers:** Chromium only. There was no screen-reader pass and no
  physical device.
