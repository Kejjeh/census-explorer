"""Download official ACS table metadata (no credential required).

Census metadata endpoints are open; only data queries need a key.  That means
the exact estimate, margin-of-error and annotation cell codes, their labels and
their universes can always be discovered from the release itself rather than
assumed.
"""

from __future__ import annotations

from pathlib import Path

from .. import provenance
from ..config import Release
from ..http_client import fetch


def group_url(release: Release, table: str) -> str:
    return f"{release.api_base}/groups/{table}.json"


def cache_rel_path(release: Release, table: str) -> str:
    return f"data/raw/metadata/{release.release_id}/groups/{table}.json"


def fetch_group(repo_root: Path, release: Release, table: str,
                manifest: provenance.Manifest) -> Path:
    """Fetch one table's metadata, cache the bytes verbatim, record provenance."""
    url = group_url(release, table)
    resp = fetch(url)
    rel = cache_rel_path(release, table)
    path = repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.body)

    manifest.add(provenance.RetrievalRecord(
        artifact_id=f"metadata:{release.release_id}:{table}",
        provider=release.provider,
        kind="metadata",
        source_url=resp.url_sanitized,
        request={"method": "GET", "release": release.release_id, "table": table},
        retrieved_at=provenance.utc_now(),
        http_status=resp.status,
        content_bytes=len(resp.body),
        sha256=provenance.sha256_bytes(resp.body),
        cache_path=rel,
        notes=[f"Official variable metadata for table {table} in {release.period_label}."],
    ))
    return path
