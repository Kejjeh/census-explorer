# Publishing the static site

GitHub Pages serves files. It runs no Python, so the local service — which is
what computes anything — cannot run there. Instead, `site build` runs the same
Python once, on your machine, and writes what it produced as plain files the
browser reads over relative URLs.

Nothing is recomputed in the browser. Every estimate, margin of error,
coefficient of variation, reliability judgement, denominator and reference
value on the published page was computed by this repository's Python during
the build. The page reshapes, counts and draws.

## What the published copy can and cannot do

| | Published site | Local service |
| --- | --- | --- |
| Map, zoom, pan, fit, click and keyboard selection | Yes | Yes |
| Topic sidebar, measure search, Shares/Counts filter | Yes, all 47 measures | Yes |
| Scope: New York City, New York State, one county's census tracts | Yes | Yes |
| Place search by name or GEOID, county, borough and tract, across the build | Yes | Yes |
| Paged table reaching every row | Yes | Yes |
| Two-place comparison chart with published 90% intervals | Yes | Yes |
| Inspector: estimate, margin of error, numerator, denominator, reliability | Yes | Yes |
| Comparing two places inside one release | Yes | Yes |
| New York City, New York State and containing-county references | Yes, precomputed during the build (one per county) | Yes |
| Reference built from the places you selected | **No** — listed and disabled, with the reason | Yes |
| Uncertainty, comparison and period panels | Yes | Yes |
| Coverage reporting and source details | Yes | Yes |
| CSV of exactly the selection | Yes, downloaded in the browser | Yes |
| Printable HTML brief | Yes — selected scope, up to 25 printed rows; CSV contains all rows | Yes |
| Export bundle (brief + CSV + figure + provenance) | **No** | Yes |
| Saved views and their input-pin check | **No**, and it does not pretend to | Yes |

The three unsupported features are named on the page itself, in a line under
the header, and in `data/manifest.json`. None of them is approximated: a
published copy cannot verify that a saved view's inputs are unchanged, so it
does not offer to save one.

## Build it

From a checkout that already has a built release in `data/processed`
(`fetch` then `build` — see `docs/USAGE.md`):

```powershell
python -m census_explorer.cli site build --base /census-explorer/ --out site
```

For the current statewide build (2019-2023, New York State), the output is:

```
release acs5_2023 (2019-2023 ACS), data mode live
wrote 113 files, 16.0 MB, 47 measures over 5474 areas, release acs5_2023
output: site/  (serve it under /census-explorer/)
```

The 5,474 areas are the state, 62 counties and 5,411 listed census tracts.
The earlier New York City-only build wrote 112 files, 5.2 MB, over 2,332
areas; that figure is historical.

To publish a single release, build from a data directory that holds only
that release (`--data-dir`, see `docs/RELEASE_NOTES.md`). The comparison
panels add a line whenever another release is present in the data
directory.

It takes several seconds and touches the network not at all. `--base` is
recorded in `data/manifest.json` for the record; every asset and data
reference in the page is relative, so the same output works at any path.

`site/` is ignored by git on purpose: the build output does not belong in the
source history.

### What `--out` will and will not replace

The build writes a whole tree, so it has to remove what was there before.
That makes `--out` the one argument that can destroy work, and it is checked
before anything is deleted.

It refuses the filesystem root, a home directory, the repository, any
directory that contains the repository, a git checkout, and a path that is a
file. It accepts a directory that does not exist or is empty.

A non-empty directory is accepted only when it is a previous build of *this*
site — proved by this project's own marker in `data/manifest.json` and the
inventory in `data/digests.json`, not by the presence of a file called
`index.html` — **and** holds nothing that build did not write. A file you put
there yourself is named in the refusal, not removed. Delete or move it if you
want the directory rebuilt.

The new site is written into a directory the build creates for itself beside
the target, so two builds cannot delete each other's work in progress and a
directory that merely happens to have a similar name is never touched. When
the new tree is complete, the previous build is moved aside and the new one
renamed into place. That is two renames, not an atomic swap: what it
guarantees is that the previous build is never deleted before the new one is
in place, and that a failure at the last step puts it back. If even that
cannot be done, the build stops and tells you where the previous copy is.

### Check it before publishing

```powershell
python -m http.server 8899 --directory .
```

Then open `http://127.0.0.1:8899/site/`. The page should load the map, and the
browser's network panel should show requests only to `site/` — no `/api/`, no
`127.0.0.1:8765`.

What `site/` contains, and nothing else:

* `index.html`, `app.css`, `app.js`, `core.js`, `static.js` — the interface.
* `data/manifest.json` — the release, the citation, the tables used, the build
  manifest id, the counts, the join reports, and what this copy cannot do.
* `data/digests.json` — a SHA-256 of every file the build wrote, so a
  published copy can be compared against the build it came from.
* `data/dataset.json`, `data/catalog.json`, `data/status.json`,
  `data/areas.json` — the measure catalog, the area list and the release.
* `data/values/*.json` — the computed estimates, margins of error and
  denominators, one file per measure.
* `data/geography/*.json` — the Census Bureau's own cartographic boundary
  files for the matching vintage.
* `data/reference/*.json`, `data/benchmarks/*.json`, `data/quality/*.json` —
  the precomputed New York City reference and the quality wording.
* `.nojekyll` — GitHub Pages otherwise runs Jekyll, which drops files whose
  names begin with an underscore.

No raw retrieval cache, no saved view, no export bundle, no credential and no
path from the build machine. `tests/test_site_build.py` asserts each of those.

## Publish it on a `gh-pages` branch

This is the reviewed path: it publishes exactly the build you just checked.

```powershell
git worktree add ../census-explorer-pages -B gh-pages
Get-ChildItem ../census-explorer-pages -Force |
    Where-Object { $_.Name -ne '.git' } | Remove-Item -Recurse -Force
Copy-Item site/* ../census-explorer-pages -Recurse -Force
Copy-Item site/.nojekyll ../census-explorer-pages -Force
cd ../census-explorer-pages
git add -A
git commit -m "Publish static Census Explorer (acs5_2023, 2019-2023 ACS)"
git push -u origin gh-pages
cd ../census-explorer
git worktree remove ../census-explorer-pages
```

On macOS or Linux:

```bash
git worktree add ../census-explorer-pages -B gh-pages
find ../census-explorer-pages -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -r site/. ../census-explorer-pages/
cd ../census-explorer-pages && git add -A \
  && git commit -m "Publish static Census Explorer (acs5_2023, 2019-2023 ACS)" \
  && git push -u origin gh-pages
cd ../census-explorer && git worktree remove ../census-explorer-pages
```

Then, once on GitHub: **Settings → Pages → Build and deployment → Source:
Deploy from a branch → Branch: `gh-pages` / `(root)` → Save**.

The site appears at `https://<owner>.github.io/census-explorer/`.

Nothing here needs the draft pull request to be merged, and nothing needs a
workflow to exist on the default branch.

## The optional workflow

`.github/workflows/pages.yml` can do the same thing in Actions, on
`workflow_dispatch` only, with `contents: read` for the build job and
`pages: write` plus `id-token: write` only for the deploy job. It uses the
official `actions/upload-pages-artifact` and `actions/deploy-pages`.

Read the caveat before using it: the runner has no `data/processed`, so the
workflow **retrieves from the Census Bureau itself**. That produces a new
retrieval with its own manifest — not the build reviewed locally. It asks you
to type `yes` to confirm for exactly that reason. No credential is used: the
Summary File transport is keyless.

Use the local path above when what you want published is the build you
checked.

## Rebuilding after a data or code change

Run `site build` again and republish. Repeated builds from the same data
directory and code revision are **not byte-identical as a whole**:

- `data/manifest.json` records `generated_at`, so it changes on every build.
- `data/digests.json` and the snapshot in `index.html` follow from it, so
  they change too, and share links made on one build are refused by the next.
- Every other file (110 of 113 for the statewide build) is byte-identical
  across builds.

`data/digests.json` in the published copy records what the build wrote, so
two builds can be compared file by file. To compare content, compare those
files, or use the content fingerprint in `docs/RELEASE_NOTES.md`, rather than
the snapshot. Rebuilding `data/processed` itself also changes `built_at` and
`code_revision` in `dataset.json`.


### Sharing a published view

Use **Share view** and copy the displayed link. It restores the release, measure,
geography level, selected scope, inspected place, two-place comparison and NYC
reference. Zoom and table sort are not included. Invalid links and links for a
different published data snapshot are refused, with a link to the current home
page. Data files are checked against their build digests as they load.

This is not a saved project: it does not pin or re-check the raw retrieval cache.
The public brief likewise is a generated snapshot, with denominators, uncertainty,
source cells and explicit reference limits. It lists at most 25 selected places
in GEOID order and says when rows are omitted; CSV always includes the full scope.
Use the browser Print command in the brief tab to print or save as PDF.
