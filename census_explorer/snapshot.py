"""Content pins for a saved project.

Recording which manifest a project was built from describes its provenance but
does not protect it: identifiers stay the same while bytes move underneath
them. A pin here is a digest of the exact files a result was computed from —
the measure values, the geometry, the dataset's measure definitions and
release metadata, and the retrieval manifests behind them.

Verification is fail-closed. If a pinned input is missing or its content has
changed, replay and export refuse with a message naming the file and what to
do about it. Nothing is re-fetched and nothing is substituted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import provenance

SCHEMA_VERSION = 1


class SnapshotMismatch(RuntimeError):
    """A pinned input is missing or no longer matches what was pinned."""


@dataclass(frozen=True)
class InputDigest:
    """One file a result depended on, and what it contained at the time."""

    role: str            # dataset | values | geometry | manifest
    path: str            # repository-relative, POSIX separators
    sha256: str
    bytes: int
    note: str = ""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Snapshot:
    """The inputs and the definitions a saved result was produced from."""

    created_at: str
    code_revision: str
    inputs: list[dict] = field(default_factory=list)
    #: The measure and release definitions as they were, so a replay does not
    #: silently pick up a later edit to the live catalog.
    definitions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, doc: dict | None) -> "Snapshot | None":
        if not doc:
            return None
        if doc.get("schema_version") != SCHEMA_VERSION:
            raise SnapshotMismatch(
                f"this project pins its inputs with snapshot schema version "
                f"{doc.get('schema_version')}, and this build reads version "
                f"{SCHEMA_VERSION}. Re-save the project to pin it again."
            )
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in doc.items() if k in known})

    @property
    def input_paths(self) -> list[str]:
        return [i["path"] for i in self.inputs]


def digest_file(repo_root: Path, rel_path: str, role: str, note: str = "") -> InputDigest:
    path = repo_root / rel_path
    if not path.exists():
        raise SnapshotMismatch(
            f"cannot pin {rel_path}: the file does not exist. Build the dataset "
            "before saving a project."
        )
    return InputDigest(
        role=role,
        path=str(rel_path).replace("\\", "/"),
        sha256=provenance.sha256_file(path),
        bytes=path.stat().st_size,
        note=note,
    )


def verify(repo_root: Path, snap: Snapshot) -> list[str]:
    """Return one problem string per input that cannot be trusted."""
    problems: list[str] = []
    for entry in snap.inputs:
        path = repo_root / entry["path"]
        if not path.exists():
            problems.append(
                f"{entry['path']} is missing; it was recorded when this project "
                f"was saved ({entry['bytes']} bytes, sha256 "
                f"{entry['sha256'][:12]}...)"
            )
            continue
        size = path.stat().st_size
        actual = provenance.sha256_file(path)
        if actual != entry["sha256"]:
            problems.append(
                f"{entry['path']} changed since the project was saved "
                f"(pinned sha256 {entry['sha256'][:12]}..., now {actual[:12]}..., "
                f"{entry['bytes']} bytes then, {size} now)"
            )
    return problems


def require(repo_root: Path, snap: Snapshot | None, project_id: str) -> None:
    """Raise unless every pinned input is present and unchanged."""
    if snap is None:
        raise SnapshotMismatch(
            f"project '{project_id}' was saved without pinned inputs, so it cannot "
            "be replayed reproducibly. Open the view you want and save it again to "
            "pin it."
        )
    problems = verify(repo_root, snap)
    if not problems:
        return
    raise SnapshotMismatch(
        f"project '{project_id}' cannot be replayed: its inputs are no longer what "
        f"it was saved from.\n  - " + "\n  - ".join(problems) +
        "\n\nNothing was substituted and nothing was re-fetched. Either restore "
        "those files from the cache they were built from, or open the view you "
        "want now and re-save the project to pin the current inputs."
    )
