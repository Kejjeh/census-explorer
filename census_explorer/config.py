"""Project configuration: releases, geography scope, tables and measures.

Configuration is data, not code.  It is validated on load so that a mistake in
a JSON file (a five-year product labelled with a single year, a measure whose
numerator and denominator come from different universes) fails immediately
rather than becoming a chart.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"

#: A five-year ACS period estimate must never be labelled with one year.
_BARE_YEAR_RE = re.compile(r"^\s*\d{4}\s*$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Release:
    """One survey release: product + reference period + geography vintage."""

    release_id: str
    provider: str
    dataset: str
    vintage: int
    period_start: int
    period_end: int
    period_label: str
    product_label: str
    boundary_release: str
    geography_vintage: str
    api_base: str
    summary_file_base: str
    citation: str

    @property
    def period_years(self) -> int:
        return self.period_end - self.period_start + 1

    @property
    def is_five_year(self) -> bool:
        return self.period_years == 5

    def key(self) -> str:
        """The dataset key: product, period, and geography vintage together."""
        return (
            f"{self.dataset}|{self.period_start}-{self.period_end}"
            f"|geo:{self.boundary_release}"
        )

    def to_json(self) -> dict:
        return {
            "release_id": self.release_id,
            "provider": self.provider,
            "dataset": self.dataset,
            "vintage": self.vintage,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "period_label": self.period_label,
            "product_label": self.product_label,
            "period_years": self.period_years,
            "boundary_release": self.boundary_release,
            "geography_vintage": self.geography_vintage,
            "dataset_key": self.key(),
            "citation": self.citation,
        }


@dataclass(frozen=True)
class MeasureDef:
    """A research measure with an explicit numerator, denominator and universe."""

    measure_id: str
    label: str
    concept: str
    unit: str                       # "persons" | "percent"
    kind: str                       # "count" | "share"
    numerator_cells: list[str]      # table cell codes, e.g. ["B06009_005", "B06009_006"]
    denominator_cells: list[str]    # empty for counts
    universe_note: str
    definition_note: str
    caveats: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    @property
    def tables(self) -> list[str]:
        cells = list(self.numerator_cells) + list(self.denominator_cells)
        return sorted({c.split("_")[0] for c in cells})

    def to_json(self) -> dict:
        return {
            "measure_id": self.measure_id,
            "label": self.label,
            "concept": self.concept,
            "unit": self.unit,
            "kind": self.kind,
            "numerator_cells": self.numerator_cells,
            "denominator_cells": self.denominator_cells,
            "universe_note": self.universe_note,
            "definition_note": self.definition_note,
            "caveats": self.caveats,
            "topics": self.topics,
            "tables": self.tables,
        }


@dataclass
class ProjectConfig:
    raw: dict[str, Any]
    releases: dict[str, Release]
    measures: dict[str, MeasureDef]

    # NYC scope
    state_fips: str = "36"
    counties: dict[str, str] = field(default_factory=dict)

    @property
    def county_geoids(self) -> list[str]:
        return [f"{self.state_fips}{c}" for c in sorted(self.counties)]

    def county_name(self, geoid: str) -> str:
        return self.counties.get(geoid[2:], geoid)

    def release(self, release_id: str) -> Release:
        try:
            return self.releases[release_id]
        except KeyError:
            raise ConfigError(
                f"unknown release '{release_id}'; configured: {sorted(self.releases)}"
            ) from None

    def composite(self, composite_id: str) -> dict | None:
        """A named group of areas whose membership is documented, not inferred."""
        return (self.raw.get("explorer", {}).get("composites") or {}).get(composite_id)

    def measures_for_release(self) -> list[MeasureDef]:
        return [self.measures[k] for k in sorted(self.measures)]

    def all_tables(self) -> list[str]:
        tables: set[str] = set()
        for m in self.measures.values():
            tables.update(m.tables)
        return sorted(tables)


def _validate_release(r: Release) -> None:
    if r.is_five_year and _BARE_YEAR_RE.match(r.period_label):
        raise ConfigError(
            f"release {r.release_id}: a five-year period estimate may not be "
            f"labelled '{r.period_label.strip()}'. Use the full range, e.g. "
            f"'{r.period_start}-{r.period_end} ACS'."
        )
    if str(r.period_start) not in r.period_label or str(r.period_end) not in r.period_label:
        raise ConfigError(
            f"release {r.release_id}: period_label '{r.period_label}' must name "
            f"both {r.period_start} and {r.period_end}."
        )
    if r.period_end < r.period_start:
        raise ConfigError(f"release {r.release_id}: period_end precedes period_start")


def _validate_measure(m: MeasureDef) -> None:
    if m.kind not in {"count", "share"}:
        raise ConfigError(f"measure {m.measure_id}: kind must be count or share")
    if not m.numerator_cells:
        raise ConfigError(f"measure {m.measure_id}: no numerator cells")
    if m.kind == "share" and not m.denominator_cells:
        raise ConfigError(
            f"measure {m.measure_id}: a share needs an explicit denominator. "
            "A denominator is part of the measure definition, not a display option."
        )
    if m.kind == "count" and m.denominator_cells:
        raise ConfigError(f"measure {m.measure_id}: a count must not carry a denominator")
    if m.kind == "share" and m.unit != "percent":
        raise ConfigError(f"measure {m.measure_id}: share measures must use unit 'percent'")
    if m.kind == "count" and m.unit != "persons":
        raise ConfigError(f"measure {m.measure_id}: count measures must use unit 'persons'")
    bad = [c for c in m.numerator_cells + m.denominator_cells
           if not re.match(r"^[A-Z0-9]+_\d{3}$", c)]
    if bad:
        raise ConfigError(f"measure {m.measure_id}: malformed cell codes {bad}")


def load(config_dir: Path | str | None = None) -> ProjectConfig:
    cdir = Path(config_dir) if config_dir else CONFIG_DIR
    with open(cdir / "project.json", "r", encoding="utf-8") as fh:
        project = json.load(fh)
    with open(cdir / "measures.json", "r", encoding="utf-8") as fh:
        measures_doc = json.load(fh)

    explorer = project.get("explorer")
    if not explorer:
        raise ConfigError("config/project.json is missing the 'explorer' section")

    releases: dict[str, Release] = {}
    for rid, spec in explorer["releases"].items():
        r = Release(release_id=rid, **spec)
        _validate_release(r)
        releases[rid] = r

    measures: dict[str, MeasureDef] = {}
    for spec in measures_doc["measures"]:
        m = MeasureDef(**spec)
        _validate_measure(m)
        if m.measure_id in measures:
            raise ConfigError(f"duplicate measure_id {m.measure_id}")
        measures[m.measure_id] = m

    first = project["first_project"]
    return ProjectConfig(
        raw=project,
        releases=releases,
        measures=measures,
        state_fips=str(first["modern_state_fips"]),
        counties=dict(first["modern_counties"]),
    )
