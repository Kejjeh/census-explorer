# Browser acceptance: an executable recipe

The offline suite (`python -B -m unittest discover -s tests -t .` and
`node --test tests/js/core.test.js`) runs on synthetic fixtures and never
opens a browser. This recipe is the other half: it drives a real browser
against the **live** 2019-2023 ACS build, through the local service and the
published static site, and records what it saw. The two are kept separate on
purpose.

**Only live builds are accepted.** Before Playwright is loaded, before an
output directory is created and before anything is rendered or reported,
each script reads every target's own status metadata: `api/status` for the
local service, `data/status.json` for the static site. It refuses the run,
with exit status 2 and nothing written, unless every target says
`data_mode: "live"`. A fixture build is refused, as is a missing or unknown
mode, an HTTP error, a response that is not a JSON object, or a target that
cannot be reached. `--preflight-only` runs just this check. The offline
suite drives it against controlled responses (`tests/test_acceptance_harness.py`);
no browser is involved. The journeys name real geographies (Erie County
36029, Suffolk County 36103, Bronx tract 36005000100), so they would be
meaningless on synthetic data anyway.

Nothing here is a dependency of this repository. The scripts load Playwright
from where it is already installed: `$PLAYWRIGHT_MODULE` if set, else
`/opt/node22/lib/node_modules/playwright/index.mjs`. Without Playwright they
stop and say so, and nothing is downloaded. The PDF step also needs the full
Chromium build (Playwright's `chromium` channel), which contains the PDF viewer.

## 1. Build and serve both targets

From a checkout with a live build in `data/processed` (see `docs/RUNBOOK.md`):

```bash
python3 -m census_explorer.cli site build --base /census-explorer/ --out /tmp/pages/census-explorer \
  --hospitals data/processed/hospitals   # only for the hospital journeys; see docs/HOSPITALS.md
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
| Hospital layer (`--only hospitals`) | 1480×1000 | Needs a built hospital registry (`hospitals fetch`, `hospitals build`, and `--hospitals` on the static build). The layer is off by default, and nothing hospital-related is requested until it is turned on. The registry loads with every reconciliation check passed. A county with an unlocated site is filtered: its located sites are exactly the markers, the summary and the table agree, and the CSV holds exactly the filtered records. The unlocated site shows its reason and has no marker. An extension site links to its main site, and no CMS rating appears on an HFIS site. A marker click opens a site and leaves the census selection alone. A CMS entity with no rating shows "Not available" with the CMS footnote; a rated one shows "n of 5" and a Care Compare link built from its CCN; an ambiguous CCN is labelled ambiguous. Turning the layer off leaves the census map, table, legend, scope and address exactly as before. |
| Hospital layer, keyboard only (`--only hospitals-keyboard`) | 390×844 | Tab to the toggle and press Space; search; reach the county filter and a directory row; Enter opens the details and moves focus to them; beds are by category and never totalled; Enter on the CSV button downloads exactly the filtered sites. Checks focus visibility, that focus never falls back to the page body, and the 390 px layout. |
| Table to result actions at 390 CSS px | 390×844 | Counts the key presses from the table's row stop to Share, Open brief and Export (or Download CSV). It counts them first by plain Tab order in both directions, with nothing inspected and again with a place inspected and two compared, and then by the skip route: Tab to "Skip to result actions", Enter, then Tab. The skip route must take at most 5 presses. The skip link must be visible and focused, and must land on the "Result actions" group. The page address must not change. Shift+Tab from the group reaches Method & sources, with no trap. "Back to the table" returns to the row that was left, or to the table heading when there are no rows. While a view is loading and after a failed load, share, brief and export stay disabled and Tab moves past them. Locally, "Saved views" stays usable, because reopening saved views is allowed, and its Save button stays disabled. `--only result-actions` runs this journey alone. |

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
- every page of the PDF produced an image, and the images are pairwise
  distinct files. The viewer ignores a `#page=` change within an open
  document, which once produced three pictures of page 1, and this catches
  that. **Distinct bytes do not prove that an image shows the page it is
  named for, or that the page was completely drawn.** Page files are opened
  through `pathToFileURL`, so paths with spaces (checked) and Windows paths
  (not checked here) form valid URLs.

**These checks are not visual acceptance, and manual inspection is
mandatory.** Open every page PNG and look at it: confirm that it shows the
page its name says, then check headings, page breaks, repeated table headers, rows split across pages,
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
