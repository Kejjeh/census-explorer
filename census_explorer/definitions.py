"""Definitions quoted verbatim from the provider's own glossary.

The difference between "foreign born" and "born outside the United States" is
not stylistic: the Census Bureau counts someone born abroad to a U.S. citizen
parent as native. Labelling a citizenship-at-birth measure as a birthplace one
describes a different population, so the wording the interface uses is checked
against the provider's glossary rather than paraphrased from memory.

Refresh with::

    python -m census_explorer.cli reference refresh
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REFERENCE_PATH = (Path(__file__).resolve().parent / "reference"
                  / "census_definitions.json")


class MissingDefinition(KeyError):
    pass


@lru_cache(maxsize=1)
def _document() -> dict[str, Any]:
    with open(REFERENCE_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def get(key: str) -> dict[str, Any]:
    try:
        return _document()["definitions"][key]
    except KeyError:
        raise MissingDefinition(
            f"no archived definition for {key!r}; run: "
            "python -m census_explorer.cli reference refresh") from None


def quote(key: str) -> str:
    return get(key)["definition"]


def all_definitions() -> dict[str, Any]:
    return dict(_document()["definitions"])
