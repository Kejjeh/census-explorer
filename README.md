# Census Explorer

A local research lab for building a personal census explorer: maps, time series, population comparisons, and reproducible exports. NYC migration and demographic change is the first project; the repository should support additional census projects later.

## Current status

Repository foundation only. No data have been downloaded, no extracts submitted, no analysis computed, and no dashboard implemented. The pipeline filenames and completion claims in the supplied project notes describe a proposed system; those files were not provided. Digitized chart values are not source data.

## Start here

- [Social Explorer research](docs/SOCIAL_EXPLORER_RESEARCH.md): feature comparison, proposed architecture, and build milestones.
- [Research plan](docs/RESEARCH_PLAN.md): eight research directions, definitions, validation gates, and staged delivery.
- [Sources and claim review](docs/SOURCES.md): official documentation and unresolved claims.
- [Project configuration](config/project.json): initial scope and explicit unresolved decisions; a planning configuration, not an executable extract.
- [Agent instructions](AGENTS.md): rules for future work.

## First implementation milestone

Build one reproducible ACS county-table pull for the five NYC boroughs, preserving original responses, table metadata, estimates, margins of error, annotations, and a manifest. Reconcile a small set of cells to the published source before adding calculations or maps. Start with the 2019-2023 ACS five-year product because it matches the supplied brief; this is an explicit reference period, not a claim that it is the latest release.

Then build a local table browser and the first shared-scale small-multiple chart. Historical microdata, harmonized maps, migration flows, and a full explorer are later milestones. No application framework or third-party dependency has been selected.

## Credentials and local data

Request your own Census key at https://api.census.gov/data/key_signup.html. IPUMS access requires an account and the applicable project permissions; its API key page is https://account.ipums.org/api_keys.

Keep keys in local environment variables named `CENSUS_API_KEY` and `IPUMS_API_KEY`. `.env.example` documents these names; no dotenv loader is installed. Never paste keys into chat or commit them. Future clients must redact keys from URLs, logs, manifests, and errors.

Use ignored `data/raw/`, `data/processed/`, and `artifacts/` folders for downloads and generated results. Preserve raw inputs immutably with checksums. Commit small synthetic fixtures and metadata separately when implementation begins. Respect source-specific access and redistribution terms.

## Verify the foundation

Run from this repository in PowerShell:

```powershell
python -m json.tool config/project.json
 git diff --cached --check
 git status --short --branch
 git check-ignore .env data/raw/example.csv.gz artifacts/example.html
```

These verify configuration and repository hygiene only. There is no runtime or analysis test suite yet.
