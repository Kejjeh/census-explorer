"""Checksums, retrieval records, and manifests.

Every byte retrieved from a provider is hashed and described before it is
used.  A manifest is the reproducible index of those records: it is what a
saved project pins so that refreshing the cache cannot silently change an
older figure.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .redact import redact, redact_structure

MANIFEST_SCHEMA_VERSION = 1


def utc_now() -> str:
    """ISO-8601 UTC timestamp with second precision."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def code_revision(repo_root: str | os.PathLike | None = None) -> str:
    """Best-effort git revision of the code that produced an artifact."""
    root = Path(repo_root or Path(__file__).resolve().parent.parent)
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if out.returncode == 0:
            rev = out.stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return rev + ("+dirty" if dirty.stdout.strip() else "")
    except Exception:  # pragma: no cover - environment without git
        pass
    return "unknown"


@dataclass
class RetrievalRecord:
    """Describes one immutable raw artifact in the cache."""

    artifact_id: str
    provider: str
    kind: str                      # metadata | observations | geography | reference
    source_url: str                # sanitized: never contains a credential
    request: dict[str, Any]        # sanitized request description
    retrieved_at: str
    http_status: int | None
    content_bytes: int
    sha256: str
    cache_path: str                # repo-relative
    # For streamed sources we keep the full-file digest separately from the
    # digest of the subset actually retained.
    upstream_full_sha256: str | None = None
    upstream_full_bytes: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return redact_structure(asdict(self))


def write_json(path: str | os.PathLike, obj: Any) -> str:
    """Write redacted JSON deterministically; return the sha256 of the bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(redact_structure(obj), indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(data)
    return sha256_bytes(data)


def read_json(path: str | os.PathLike) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class Manifest:
    manifest_id: str
    created_at: str
    code_revision: str
    data_mode: str                       # live | fixture
    description: str
    records: list[dict] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    schema_version: int = MANIFEST_SCHEMA_VERSION

    @classmethod
    def new(cls, manifest_id: str, description: str, data_mode: str,
            repo_root: str | os.PathLike | None = None) -> "Manifest":
        return cls(
            manifest_id=manifest_id,
            created_at=utc_now(),
            code_revision=code_revision(repo_root),
            data_mode=data_mode,
            description=description,
        )

    def add(self, record: RetrievalRecord) -> None:
        self.records.append(record.to_json())

    def save(self, path: str | os.PathLike) -> str:
        return write_json(path, redact_structure(asdict(self)))

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Manifest":
        raw = read_json(path)
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def verify(self, repo_root: str | os.PathLike) -> list[str]:
        """Re-hash every cached artifact.  Returns a list of problem strings."""
        problems: list[str] = []
        root = Path(repo_root)
        for rec in self.records:
            p = root / rec["cache_path"]
            if not p.exists():
                problems.append(f"missing cached artifact: {rec['cache_path']}")
                continue
            actual = sha256_file(p)
            if actual != rec["sha256"]:
                problems.append(
                    f"checksum mismatch for {rec['cache_path']}: "
                    f"manifest={rec['sha256'][:12]} actual={actual[:12]}"
                )
            if p.stat().st_size != rec["content_bytes"]:
                problems.append(
                    f"size mismatch for {rec['cache_path']}: "
                    f"manifest={rec['content_bytes']} actual={p.stat().st_size}"
                )
        return [redact(x) for x in problems]
