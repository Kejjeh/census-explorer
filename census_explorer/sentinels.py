"""ACS estimate and margin-of-error annotation values ("sentinels").

The Census Bureau publishes specific negative integers in numeric fields to
carry a *meaning*, not a measurement.  Treating ``-555555555`` as a count is
one of the classic ways to corrupt an ACS analysis, so every value that enters
this project is classified here before anything arithmetic happens to it.

The value table is not hard-coded from memory: it is extracted from the
official Census developer documentation and stored in
``census_explorer/reference/acs_annotation_values.json`` with the source URL,
retrieval timestamp and document checksum.  Refresh it with::

    python -m census_explorer.cli reference refresh
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

REFERENCE_PATH = Path(__file__).resolve().parent / "reference" / "acs_annotation_values.json"


class MissingReferenceError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def annotation_table() -> dict[str, dict[str, str]]:
    """Return {sentinel_value_as_string: {symbol, meaning}} from the archive."""
    if not REFERENCE_PATH.exists():  # pragma: no cover - repo ships the file
        raise MissingReferenceError(
            f"annotation reference not found at {REFERENCE_PATH}; "
            "run: python -m census_explorer.cli reference refresh"
        )
    with open(REFERENCE_PATH, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return {str(k): v for k, v in doc["values"].items()}


def reference_provenance() -> dict[str, Any]:
    with open(REFERENCE_PATH, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    return doc["provenance"]


#: The one annotation whose published meaning is an instruction about
#: arithmetic rather than an absence of information: a controlled estimate has
#: no sampling error, and the documentation states the margin of error may be
#: treated as zero.  It is handled explicitly and always flagged; every other
#: annotation makes the value unavailable.
CONTROLLED_MOE_CODE = "-555555555"


def is_controlled(cell: "Cell | None") -> bool:
    return cell is not None and (cell.raw or "").strip() == CONTROLLED_MOE_CODE


#: Status of a single cell after classification.
OK = "ok"
ANNOTATED = "annotated"      # a documented sentinel: a meaning, not a number
MISSING = "missing"          # empty / null in the source
UNPARSEABLE = "unparseable"  # present but not a number and not a known sentinel


@dataclass(frozen=True)
class Cell:
    """One classified source value.

    ``value`` is a number only when ``status == OK``.  In every other case it
    is ``None`` and the reason travels alongside it.  Nothing in this project
    may substitute ``0`` for ``None``.
    """

    raw: str | None
    status: str
    value: float | None = None
    symbol: str | None = None
    meaning: str | None = None

    @property
    def is_number(self) -> bool:
        return self.status == OK and self.value is not None

    def to_json(self) -> dict:
        return {
            "raw": self.raw,
            "status": self.status,
            "value": self.value,
            "symbol": self.symbol,
            "meaning": self.meaning,
        }


def classify(raw: Any) -> Cell:
    """Classify one raw source value.

    Accepts the string forms produced by the Census API (JSON strings, ``null``)
    and by the table-based Summary File (pipe-delimited text).
    """
    if raw is None:
        return Cell(raw=None, status=MISSING, meaning="value absent from source")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = repr(raw) if isinstance(raw, float) else str(raw)
    if not isinstance(raw, str):
        return Cell(raw=str(raw), status=UNPARSEABLE, meaning="unexpected value type")

    text = raw.strip()
    if text == "":
        return Cell(raw=raw, status=MISSING, meaning="empty value in source")

    table = annotation_table()
    if text in table:
        entry = table[text]
        return Cell(
            raw=raw,
            status=ANNOTATED,
            value=None,
            symbol=entry.get("symbol"),
            meaning=entry.get("meaning"),
        )

    try:
        number = float(text)
    except ValueError:
        return Cell(raw=raw, status=UNPARSEABLE, meaning="value is not numeric")

    if number.is_integer():
        number = int(number)

    # Defence in depth: an undocumented nine-digit negative repunit-style code
    # is far more likely to be a new annotation than a real measurement.
    if isinstance(number, int) and number <= -111111111 and len(str(abs(number))) == 9:
        digits = set(str(abs(number)))
        if len(digits) == 1:
            return Cell(
                raw=raw,
                status=UNPARSEABLE,
                meaning=(
                    "looks like an undocumented Census annotation code; refresh "
                    "the annotation reference before using this value"
                ),
            )

    return Cell(raw=raw, status=OK, value=number)
