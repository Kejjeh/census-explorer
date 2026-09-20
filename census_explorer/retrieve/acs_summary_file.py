"""Retrieve ACS estimates from the official table-based Summary File.

This is the keyless path.  The Census Bureau publishes each detailed table as
one pipe-delimited file covering every published geography; no API key is
required.  The files are large (the 2023 five-year B05006 file is about
265 MiB), so they are streamed and filtered to the requested GEO_IDs.

What is preserved:

* the header line and the matching data lines, byte-for-byte, as the raw cache;
* the SHA-256 and byte length of the **entire** upstream document, computed
  while streaming, so the download can be re-verified later;
* the exact filter that was applied.

Nothing is parsed, coerced or cleaned at this stage.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from .. import provenance
from ..config import Release
from ..http_client import stream_lines
from ..redact import RedactedError

#: Summary File geography prefixes for the levels this project supports.
SUMMARY_LEVEL_PREFIX = {"county": "0500000US", "tract": "1400000US",
                        "place": "1600000US"}


def table_url(release: Release, table: str) -> str:
    return f"{release.summary_file_base}/acsdt5y{release.vintage}-{table.lower()}.dat"


def cache_rel_path(release: Release, table: str, name_suffix: str = "") -> str:
    """Where one retrieval's rows are cached.

    ``name_suffix`` keeps different geographic selections of the same table in
    different files: a later, narrower pull must never overwrite an earlier one.
    """
    stem = f"{table}{('_' + name_suffix) if name_suffix else ''}"
    return f"data/raw/acs/{release.release_id}/summary_file/{stem}.psv"


def geoid_prefixes(level: str, geoids: Iterable[str]) -> list[bytes]:
    """Byte prefixes that select the requested geographies.

    A county prefix also selects that county's tracts, because a tract GEO_ID
    embeds its county FIPS.  Selection is therefore done per level.
    """
    prefix = SUMMARY_LEVEL_PREFIX[level]
    return [f"{prefix}{g}".encode("ascii") for g in geoids]


def fetch_table(repo_root: Path, release: Release, table: str,
                selections: list[tuple[str, list[str]]],
                manifest: provenance.Manifest,
                progress=None, name_suffix: str = "") -> Path:
    """Stream one Summary File table and keep only the selected geographies.

    ``selections`` is a list of ``(level, geoids)`` pairs, e.g.
    ``[("county", ["36005", ...]), ("tract", ["36005", ...])]`` where tract
    GEO_IDs are matched by their county prefix.
    """
    url = table_url(release, table)
    wanted: list[bytes] = []
    for level, geoids in selections:
        wanted.extend(geoid_prefixes(level, geoids))

    hasher = hashlib.sha256()
    total = 0

    def on_chunk(chunk: bytes) -> None:
        nonlocal total
        hasher.update(chunk)
        total += len(chunk)

    header: bytes | None = None
    kept: list[bytes] = []
    for line in stream_lines(url, on_chunk=on_chunk):
        line = line.rstrip(b"\r")
        if header is None:
            header = line
            continue
        if any(line.startswith(p) for p in wanted):
            kept.append(line)

    if header is None:
        raise RedactedError(f"empty Summary File response for {table} ({release.release_id})")
    if not header.startswith(b"GEO_ID|"):
        raise RedactedError(
            f"unexpected Summary File header for {table}: {header[:60]!r}; "
            "the published format changed and the reader must be reviewed"
        )

    rel = cache_rel_path(release, table, name_suffix)
    path = repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"\n".join([header] + kept) + b"\n"
    path.write_bytes(body)

    manifest.add(provenance.RetrievalRecord(
        artifact_id=(f"observations:{release.release_id}:{table}:summary_file"
                     f"{(':' + name_suffix) if name_suffix else ''}"),
        provider=release.provider,
        kind="observations",
        source_url=url,
        request={
            "method": "GET",
            "transport": "table-based Summary File",
            "release": release.release_id,
            "period_label": release.period_label,
            "table": table,
            "filter": [{"level": lvl, "geoid_prefixes": list(g)} for lvl, g in selections],
        },
        retrieved_at=provenance.utc_now(),
        http_status=200,
        content_bytes=len(body),
        sha256=provenance.sha256_bytes(body),
        cache_path=rel,
        upstream_full_sha256=hasher.hexdigest(),
        upstream_full_bytes=total,
        notes=[
            f"Retained {len(kept)} of the published rows for the requested geographies.",
            "Rows are verbatim source lines; the digest of the complete upstream file is "
            "recorded so the download can be re-verified.",
        ],
    ))
    if progress:
        progress(f"{table}: kept {len(kept)} rows from {total/1e6:.1f} MB upstream")
    return path
