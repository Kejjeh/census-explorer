"""The offline model of a release's published variable metadata.

Reads the cached official group metadata and exposes, per table cell, the exact
estimate / margin-of-error / annotation variable names, the label, the concept
and the universe.  Nothing here guesses a code: if a cell is not in the
release, asking for it is an error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import MeasureDef, Release

#: A table cell as this project names it: ``B05002_013`` (no E/M suffix).
CELL_RE = re.compile(r"^([A-Z0-9]+)_(\d{3})$")


class MetadataError(ValueError):
    pass


@dataclass(frozen=True)
class CellMeta:
    cell: str                 # B05002_013
    table: str
    estimate_var: str         # B05002_013E
    moe_var: str | None       # B05002_013M
    estimate_annotation_var: str | None
    moe_annotation_var: str | None
    label: str
    label_path: list[str]
    concept: str
    universe: str
    predicate_type: str

    def to_json(self) -> dict:
        return {
            "cell": self.cell,
            "table": self.table,
            "estimate_var": self.estimate_var,
            "moe_var": self.moe_var,
            "estimate_annotation_var": self.estimate_annotation_var,
            "moe_annotation_var": self.moe_annotation_var,
            "label": self.label,
            "label_path": self.label_path,
            "concept": self.concept,
            "universe": self.universe,
        }


@dataclass
class ReleaseMetadata:
    release_id: str
    tables: dict[str, dict[str, CellMeta]] = field(default_factory=dict)
    table_universe: dict[str, str] = field(default_factory=dict)
    table_concept: dict[str, str] = field(default_factory=dict)
    source_files: dict[str, str] = field(default_factory=dict)  # table -> cache path

    def cell(self, code: str) -> CellMeta:
        m = CELL_RE.match(code)
        if not m:
            raise MetadataError(f"malformed cell code {code!r}")
        table = m.group(1)
        if table not in self.tables:
            raise MetadataError(
                f"table {table} metadata not cached for release {self.release_id}"
            )
        try:
            return self.tables[table][code]
        except KeyError:
            raise MetadataError(
                f"cell {code} does not exist in table {table} for release "
                f"{self.release_id}. Codes are never invented: check the published "
                f"table shell for this release."
            ) from None

    def has_cell(self, code: str) -> bool:
        try:
            self.cell(code)
            return True
        except MetadataError:
            return False


def _clean_label(raw: str) -> tuple[str, list[str]]:
    parts = [p for p in raw.split("!!")]
    if parts and parts[0] in ("Estimate", "Margin of Error"):
        parts = parts[1:]
    parts = [p.rstrip(":") for p in parts if p not in ("", None)]
    return (" / ".join(parts) if parts else raw), parts


def load_release_metadata(repo_root: Path, release: Release,
                          tables: list[str]) -> ReleaseMetadata:
    """Build the metadata model from cached official group files."""
    out = ReleaseMetadata(release_id=release.release_id)
    for table in tables:
        rel = f"data/raw/metadata/{release.release_id}/groups/{table}.json"
        path = repo_root / rel
        if not path.exists():
            raise MetadataError(
                f"metadata for {table} ({release.release_id}) is not cached at {rel}. "
                "Run: python -m census_explorer.cli fetch metadata"
            )
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        variables: dict[str, Any] = doc.get("variables", {})
        cells: dict[str, CellMeta] = {}
        for var, spec in variables.items():
            m = re.match(rf"^{re.escape(table)}_(\d{{3}})E$", var)
            if not m:
                continue
            code = f"{table}_{m.group(1)}"
            moe = f"{code}M" if f"{code}M" in variables else None
            ea = f"{code}EA" if f"{code}EA" in variables else None
            ma = f"{code}MA" if f"{code}MA" in variables else None
            label, path_parts = _clean_label(spec.get("label", ""))
            cells[code] = CellMeta(
                cell=code, table=table, estimate_var=var, moe_var=moe,
                estimate_annotation_var=ea, moe_annotation_var=ma,
                label=label, label_path=path_parts,
                concept=spec.get("concept", ""),
                universe=spec.get("universe", ""),
                predicate_type=spec.get("predicateType", ""),
            )
        if not cells:
            raise MetadataError(f"no estimate cells found in cached metadata for {table}")
        universes = {c.universe for c in cells.values()}
        if len(universes) != 1:
            raise MetadataError(
                f"table {table} reports more than one universe {sorted(universes)}; "
                "a table with mixed universes cannot be used without review"
            )
        out.tables[table] = cells
        out.table_universe[table] = universes.pop()
        out.table_concept[table] = next(iter(cells.values())).concept
        out.source_files[table] = rel
    return out


def verify_measures(meta: ReleaseMetadata, measures: list[MeasureDef]) -> list[str]:
    """Check every configured measure against the release. Returns problems."""
    problems: list[str] = []
    for m in measures:
        cells = list(m.numerator_cells) + list(m.denominator_cells)
        missing = [c for c in cells if not meta.has_cell(c)]
        if missing:
            problems.append(
                f"{m.measure_id}: cells not present in release {meta.release_id}: {missing}"
            )
            continue
        if m.kind == "share":
            nu = {meta.cell(c).universe for c in m.numerator_cells}
            du = {meta.cell(c).universe for c in m.denominator_cells}
            if nu != du:
                problems.append(
                    f"{m.measure_id}: numerator universe {sorted(nu)} does not match "
                    f"denominator universe {sorted(du)}. A share across different "
                    "universes is not a share."
                )
        num_missing_moe = [c for c in cells if meta.cell(c).moe_var is None]
        if num_missing_moe:
            problems.append(
                f"{m.measure_id}: no margin-of-error variable published for {num_missing_moe}"
            )
    return problems
