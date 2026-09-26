# Dependencies

## What this project uses

Nothing outside the Python standard library and the browser's own platform.

| Need | Chosen | Instead of |
| --- | --- | --- |
| HTTP retrieval | `urllib.request` | `requests`, `httpx` |
| Analytical storage | JSON and CSV on disk | DuckDB, Parquet, SQLite |
| Boundary files | a shapefile/dBASE reader in `census_explorer/shapefile.py` | `geopandas`, `fiona`, `pyshp`, `shapely` |
| Local service | `http.server.ThreadingHTTPServer` | Flask, FastAPI, uvicorn |
| Tests | `unittest` | pytest |
| Map rendering | inline SVG built by hand, in the page and on the service side | MapLibre GL JS, Leaflet, D3 |
| Interface | plain HTML, CSS and JavaScript | React, a bundler, a CSS framework |
| Fonts and styles | system font stacks, local CSS | Google Fonts, a CDN |

| Browser-logic tests | Node's built-in `node --test`, skipped when Node is absent | Jest, Vitest, Mocha, jsdom |

The repository rule is to ask before adding a dependency. Nothing was added, so
nothing was asked for.

Node is worth naming explicitly, because it is the one tool mentioned above
that is not already on every machine. It is **not** a dependency: nothing in
the Python package, the service, the data pipeline or the browser needs it, and
`pip`, `unittest` and the app all work without it. It is only how
`web/core.js` — the page logic that has no DOM and no network — runs its own
tests. `tests/test_ui_core.py` skips with an explanation when Node is not
installed, in the same way the live network tests do. This was not a purity exercise: a zero-dependency build
is what makes "no network access during import, tests, or rendering" simple to
guarantee, and what lets the browser interface run on a machine that has never
been online.

## The open decision

`docs/SOCIAL_EXPLORER_RESEARCH.md` recommended DuckDB with Parquet for
analytical storage and MapLibre GL JS for the map. Both remain sensible and
both remain **unrequested and uninstalled**. If you want them, these are the
exact packages, what they would buy, and what they would cost.

### DuckDB + Parquet

- Packages: `duckdb` (Python wheel; bundles its own engine, no server).
  Parquet support is built in, so `pyarrow` is not required for this use.
- Buys: column and predicate pushdown over much larger slices than NYC —
  a statewide or national pull, many releases at once, or ad-hoc SQL against
  the observation table. Materially faster than re-reading JSON once the
  dataset outgrows memory.
- Costs: a compiled wheel per platform; a second storage format to keep in
  step with the manifest and checksum rules; and a re-verification that the
  sentinel/annotation distinction survives a typed column, since Parquet will
  happily store `-999999999` as an integer.
- Verdict: **worth revisiting when a build exceeds roughly a million
  observations.** The current NYC build is about 110,000 values per release
  and loads in well under a second.

### MapLibre GL JS

- Packages: `maplibre-gl` (vendored locally, not from a CDN, to keep offline
  replay working).
- Buys: smooth pan and zoom, vector tiles, and a far better experience at
  tract scale and below.
- Costs: about 800 KB of vendored JavaScript to review and keep current; a
  tile build step if vector tiles are used; and a licensing and privacy
  decision if any hosted basemap is added. The current inline-SVG map draws
  2,324 tract polygons without difficulty.
- Verdict: **worth revisiting when a level below census tract, or a
  multi-county extent, is added.**

### What would need approval first

Adding either package changes the offline guarantees this build rests on, so it
should be an explicit decision with its own review, not a convenience import.
Until then, no code in this repository may `import` a third-party module, and
no page may load a script from a CDN.
