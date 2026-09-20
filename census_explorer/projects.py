"""Saved project definitions.

A saved project records the *requested* definition — release, measure,
geography level, areas, class breaks — together with the manifest identifiers
of the data it was built from.  Pinning the manifest is the point: refreshing
the cache later must not silently change a figure that was saved earlier.

Projects are plain JSON files under ``data/projects``.  They contain no
credentials and no personal data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import provenance
from .snapshot import Snapshot

SCHEMA_VERSION = 2
PROJECT_DIR = "data/projects"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ProjectError(ValueError):
    pass


def validate_id(project_id: str) -> str:
    if not _ID_RE.match(project_id or ""):
        raise ProjectError(
            f"invalid project id {project_id!r}: use lower-case letters, digits, "
            "dot, dash or underscore (max 64 characters)"
        )
    return project_id


@dataclass
class SavedProject:
    project_id: str
    title: str
    release_id: str
    level: str
    measure_id: str
    areas: list[str] = field(default_factory=list)      # empty means "all in level"
    comparison_release_id: str | None = None
    classes: int = 5
    cut_points: list[float] | None = None
    notes: str = ""
    manifest_ids: list[str] = field(default_factory=list)
    dataset_code_revision: str = ""
    created_at: str = ""
    updated_at: str = ""
    # The definition exactly as requested, preserved even if the interface later
    # offers a substitute.  Any accepted substitution is appended, never applied
    # in place.
    requested: dict[str, Any] = field(default_factory=dict)
    substitutions: list[dict[str, Any]] = field(default_factory=list)
    #: Digests of the exact files this project was built from, plus the measure
    #: and release definitions as they were. Without this a project records
    #: where its numbers came from but cannot tell whether they still say the
    #: same thing, so replay and export refuse when it is absent.
    snapshot: dict[str, Any] | None = None
    schema_version: int = SCHEMA_VERSION

    def pin(self) -> Snapshot | None:
        return Snapshot.from_json(self.snapshot)

    def to_json(self) -> dict:
        return asdict(self)


def path_for(repo_root: Path, project_id: str) -> Path:
    return repo_root / PROJECT_DIR / f"{validate_id(project_id)}.json"


def save(repo_root: Path, project: SavedProject) -> Path:
    validate_id(project.project_id)
    now = provenance.utc_now()
    project.created_at = project.created_at or now
    project.updated_at = now
    project.dataset_code_revision = (
        project.dataset_code_revision or provenance.code_revision(repo_root)
    )
    if not project.requested:
        project.requested = {
            "release_id": project.release_id,
            "comparison_release_id": project.comparison_release_id,
            "level": project.level,
            "measure_id": project.measure_id,
            "areas": list(project.areas),
        }
    path = path_for(repo_root, project.project_id)
    provenance.write_json(path, project.to_json())
    return path


def load(repo_root: Path, project_id: str) -> SavedProject:
    path = path_for(repo_root, project_id)
    if not path.exists():
        raise ProjectError(f"no saved project '{project_id}' at {path}")
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ProjectError(
            f"project '{project_id}' uses schema version {doc.get('schema_version')}, "
            f"this build reads version {SCHEMA_VERSION}. Projects saved before "
            "inputs were pinned cannot be replayed reproducibly; open the view you "
            "want and save it again."
        )
    known = SavedProject.__dataclass_fields__
    return SavedProject(**{k: v for k, v in doc.items() if k in known})


def listing(repo_root: Path) -> list[dict]:
    d = repo_root / PROJECT_DIR
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            p = load(repo_root, path.stem)
        except ProjectError:
            continue
        out.append({
            "project_id": p.project_id, "title": p.title, "release_id": p.release_id,
            "comparison_release_id": p.comparison_release_id, "level": p.level,
            "measure_id": p.measure_id, "updated_at": p.updated_at,
            "area_count": len(p.areas),
            "pinned_input_count": len((p.snapshot or {}).get("inputs", [])),
        })
    return out


def delete(repo_root: Path, project_id: str) -> bool:
    path = path_for(repo_root, project_id)
    if path.exists():
        path.unlink()
        return True
    return False
