# Browser acceptance: an executable recipe

The offline suite (`python -B -m unittest discover -s tests -t .` and
`node --test tests/js/core.test.js`) runs on synthetic fixtures and never
opens a browser. This recipe is the other half: it drives a real browser
against the **live** 2019-2023 ACS build, through the local service and the
published static site, and records what it saw. The two are kept separate on
purpose. The scripts refuse a fixture build, because their journeys name real
geographies (Erie County 36029, Suffolk County 36103, Bronx tract
36005000100).

Nothing here is a dependency of this repository. The scripts load Playwright
from where it is already installed: `$PLAYWRIGHT_MODULE` if set, else
`/opt/node22/lib/node_modules/playwright/index.mjs`. Without Playwright they
stop and say so, and nothing is downloaded. The PDF step also needs the full
Chromium build (Playwright's `chromium` channel), which contains the PDF viewer.

## 1. Build and serve both targets

From a checkout with a live build in `data/processed` (see `docs/RUNBOOK.md`):

```bash
python3 -m census_explorer.cli site build --base /census-explorer/ --out /tmp/pages/census-explorer
(cd /tmp/pages && python3 -m http.server 8899 --bind 127.0.0.1) &
python3 -m census_explorer.cli serve &            # http://127.0.0.1:8765/
```

The static site is served under `/census-explorer/`, as it is on GitHub
Pages, so relative-path mistakes show up here rather than after publishing.

## 2. Journeys, 390 px layout and keyboard

```bash
node tests/acceptance/journeys.mjs \
  --local  http://127.0.0.1:8765/ \
  --static http://127.0.0.1:8899/census-explorer/
```

For each target:

| Journey | Viewport | What is checked |
| --- | --- | --- |
| County to tract | 1480×1000 | Starter cards name the New York City scope. New York State shows 62 counties; a city search explains that cities are not geographies. After Erie County is inspected and compared, "Explore this county's census tracts" clears both before anything loads and holds Share/brief until the 261 tracts load (260 drawn; one water tract has no boundary). The static copy then produces a share link. |
| Comparison across a scope change | 1480×1000 | Two Erie tracts are compared, and the chart draws both published intervals on an axis that includes zero. After switching to Suffolk, both are kept and labelled outside the view. The narrowing action names the Erie scope it switches to, and it lands on exactly the two tracts, with no refused load. |
| Share and reload (static only) | 1480×1000 | A version 2 link restores the scope, the reference and an inspected place outside the view, and survives a browser reload. A version 1 link opens New York City, not the state. A version 1 link naming an Erie tract is refused with a notice, and so is a link from another snapshot. |
| Genuine load failure | 1480×1000 | One values request is aborted. The view clears, and the brief and export are disabled. "Try again", pressed from the keyboard, recovers the requested measure and puts focus on the scope line. |
| Layout at 390 CSS px | 390×844 | Measured `innerWidth`, `clientWidth` and `devicePixelRatio`; no horizontal page overflow; no visible control extends past the viewport or cuts off its own label. Controls under 24 CSS px are listed as notes. Checked on the first view, on New York State counties, and on Erie tracts with a comparison. |
| Keyboard only at 390 CSS px | 390×844 | Tab, Shift+Tab, Enter, Space, arrows and typing only. The run chooses New York State, finds Erie County by search, explores its tracts, chooses a measure, uses the table (one tab stop), compares two tracts, and then uses Share (static), Open brief and Download CSV or Export. Every stop reports how many Tab presses it took and whether focus was visible. Focus must never fall back to the page body when the pressed control is replaced. |

Output goes to `artifacts/acceptance/journeys-<stamp>/` (git-ignored):
`report.md`, `report.json` and screenshots. The exit status is non-zero if
any check fails. The static checks also fail on any request outside the
site's origin, and every target fails on an unexpected console error.

## 3. Printed briefs, page by page

```bash
node tests/acceptance/briefs.mjs \
  --local  http://127.0.0.1:8765/ \
  --static http://127.0.0.1:8899/census-explorer/
```

Two briefs from each target: the Bronx, naturalised share of the
foreign-born, with the New York City reference; and New York City's census
tracts, Dominican-Republic share of the foreign-born, whose table lists 25 of
2,327 rows. Each is printed to an A4 PDF by Chromium's print engine, the one
behind "Save as PDF" in its print dialog. Every page of that PDF is then opened
in Chromium's PDF viewer and saved as a PNG. The published brief is read
back from the blob the page creates, instead of opening a blob tab.

The script checks that:

- nothing is wider than the printed page, and no table cell clips its text;
- the denominator, uncertainty, source and period are printed;
- every page was rendered, and each page image is a different page. The
  viewer ignores a `#page=` change within an open document, which once
  produced three pictures of page 1.

**These checks are not visual acceptance.** Open the page PNGs and look at
them: headings, page breaks, repeated table headers, rows split across pages,
figures, and anything drawn over anything else.

## What this recipe does not establish

- One browser engine (Chromium). Firefox and Safari are not exercised.
- Emulated viewports in headless Chromium, not a physical phone. Touch
  gestures on the map are not exercised.
- No screen reader. Focus order and visibility are checked; announcements
  are not.
- PDF pages are drawn by Chromium's own viewer, from a PDF Chromium printed.
  Another browser's print engine, or a physical printer, may paginate
  differently.
