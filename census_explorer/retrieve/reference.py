"""Archive official reference documentation used for value semantics.

Currently: the Census developer page that defines ACS estimate and
margin-of-error annotation values.  The page is cached verbatim with a
checksum, and the machine-readable extraction is written next to the code so
the project can classify values with no network access at all.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .. import provenance
from ..http_client import fetch
from ..redact import RedactedError

ANNOTATION_DOC_URL = (
    "https://www.census.gov/data/developers/data-sets/acs-1year/"
    "notes-on-acs-estimate-and-annotation-values.html"
)

NATIVITY_GLOSSARY_URL = (
    "https://www.census.gov/topics/population/foreign-born/about/glossary.html"
)

#: Definitions we quote in the interface. Each is pulled from the provider's own
#: glossary rather than paraphrased, because the difference between "foreign
#: born" and "born outside the United States" is exactly the kind of paraphrase
#: that turns a measure into a different measure.
QUOTED_DEFINITIONS = {
    "foreign_born": {
        "url": NATIVITY_GLOSSARY_URL,
        "anchor": "Foreign-born population",
        # Anchored on the closing phrase: a full stop is no sentence boundary
        # in text full of "U.S.".
        "pattern": r"The foreign-born population is composed of.{0,200}?"
                   r"through naturalization\.",
    },
    "native_born": {
        "url": NATIVITY_GLOSSARY_URL,
        "anchor": "native-born population",
        "pattern": r"the native-born population, which comprises.{0,400}?"
                   r"U\.S\. citizen parent or parents\.",
    },
}

_SENTINEL_RE = re.compile(r"^-\d{9}$")


def _cell_text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").strip()


def parse_annotation_values(page_html: str) -> dict[str, dict[str, str]]:
    """Extract ``{sentinel: {symbol, meaning}}`` from the official page.

    The page presents the values as a three-column table: value, displayed
    symbol, meaning.  A parse that finds nothing is an error, not an empty
    result: silently shipping an empty annotation table would let sentinels
    through as numbers.
    """
    values: dict[str, dict[str, str]] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        cells = [_cell_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        cells = [re.sub(r"\s+", " ", c) for c in cells]
        if len(cells) < 3:
            continue
        value = cells[0].strip()
        if not _SENTINEL_RE.match(value):
            continue
        values[value] = {"symbol": cells[1].strip(), "meaning": cells[2].strip()}
    if not values:
        raise RedactedError(
            "no annotation values parsed from the Census documentation page; "
            "the page layout changed and the extractor must be reviewed"
        )
    return values


def _visible_text(page_html: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", page_html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text)


def refresh_definitions(repo_root: Path, manifest: provenance.Manifest) -> Path:
    """Archive the provider glossary and extract the definitions we quote."""
    resp = fetch(NATIVITY_GLOSSARY_URL)
    cache_rel = Path("data/raw/reference/census_nativity_glossary.html")
    cache_path = repo_root / cache_rel
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.body)

    record = provenance.RetrievalRecord(
        artifact_id="reference:census_nativity_glossary",
        provider="US Census Bureau",
        kind="reference",
        source_url=resp.url_sanitized,
        request={"method": "GET"},
        retrieved_at=provenance.utc_now(),
        http_status=resp.status,
        content_bytes=len(resp.body),
        sha256=provenance.sha256_bytes(resp.body),
        cache_path=str(cache_rel).replace("\\", "/"),
        notes=["Provider glossary for nativity and citizenship-at-birth terms."],
    )
    manifest.add(record)

    text = _visible_text(resp.body.decode("utf-8", errors="replace"))
    out = {"schema_version": 1, "definitions": {}}
    for key, spec in QUOTED_DEFINITIONS.items():
        match = re.search(spec["pattern"], text)
        if not match:
            raise RedactedError(
                f"could not extract the '{key}' definition from "
                f"{spec['url']}; the glossary wording changed and the quoted "
                "definition must be reviewed rather than guessed at")
        out["definitions"][key] = {
            "definition": match.group(0).strip(),
            "source_url": spec["url"],
            "retrieved_at": record.retrieved_at,
            "document_sha256": record.sha256,
        }
    target = (Path(__file__).resolve().parent.parent / "reference"
              / "census_definitions.json")
    target.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    return target


def refresh(repo_root: Path, manifest: provenance.Manifest) -> Path:
    """Download, cache and re-extract the annotation reference."""
    resp = fetch(ANNOTATION_DOC_URL)
    cache_rel = Path("data/raw/reference/acs_annotation_values.html")
    cache_path = repo_root / cache_rel
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.body)

    record = provenance.RetrievalRecord(
        artifact_id="reference:acs_annotation_values",
        provider="US Census Bureau",
        kind="reference",
        source_url=resp.url_sanitized,
        request={"method": "GET"},
        retrieved_at=provenance.utc_now(),
        http_status=resp.status,
        content_bytes=len(resp.body),
        sha256=provenance.sha256_bytes(resp.body),
        cache_path=str(cache_rel).replace("\\", "/"),
        notes=["Defines ACS estimate/MOE annotation values used to classify cells."],
    )
    manifest.add(record)

    page = resp.body.decode("utf-8", errors="replace")
    values = parse_annotation_values(page)
    out = {
        "schema_version": 1,
        "provenance": {
            "source_url": ANNOTATION_DOC_URL,
            "retrieved_at": record.retrieved_at,
            "document_sha256": record.sha256,
            "document_bytes": record.content_bytes,
            "extractor": "census_explorer.retrieve.reference.parse_annotation_values",
            "note": (
                "Values are transcribed from the official Census developer "
                "documentation; they are meanings, never measurements."
            ),
        },
        "values": dict(sorted(values.items())),
    }
    target = Path(__file__).resolve().parent.parent / "reference" / "acs_annotation_values.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
