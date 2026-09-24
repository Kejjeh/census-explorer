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
SUMMARY_LEVEL_PREFIX = {"state": "0400000US", "county": "0500000US",
                        "tract": "1400000US", "place": "1600000US"}


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
    embeds its county FIPS.  Selection is therefore done per level.  A
    two-digit state FIPS given as the prefix selects every row of that level
    in the state, which is how a statewide pull is expressed.
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


# ---------------------------------------------------------------------------
# The release's own geography roster
# ---------------------------------------------------------------------------

#: The summary levels the roster is kept for: state, county, census tract.
ROSTER_SUMMARY_LEVELS = {b"040": "state", b"050": "county", b"140": "tract"}


def roster_url(release: Release) -> str:
    """The official list of every geography published in this release.

    It sits beside the data files, in the release's documentation directory,
    and names each geography by the same GEO_ID the tables use.
    """
    base = release.summary_file_base.rsplit("/data/", 1)[0]
    return f"{base}/documentation/Geos{release.vintage}{release.period_years}YR.txt"


def roster_cache_rel_path(release: Release, state_fips: str) -> str:
    return (f"data/raw/acs/{release.release_id}/summary_file/"
            f"Geos{release.vintage}{release.period_years}YR_state{state_fips}.psv")


def fetch_roster(repo_root: Path, release: Release, state_fips: str,
                 manifest: provenance.Manifest, progress=None) -> Path:
    """Stream the release's geography file and keep one state's rows.

    This is how coverage is proved rather than assumed. A table download that
    succeeds says only that some rows arrived; this file says which
    geographies the release publishes at all, so a missing row can be told
    apart from a geography the release never had.

    Kept: the header, and every row whose STATE field is this state and whose
    SUMLEVEL is state, county or census tract, with COMPONENT 00 (the whole
    geography, not an urban or rural part of it). Rows are verbatim.
    """
    url = roster_url(release)
    hasher = hashlib.sha256()
    total = 0

    def on_chunk(chunk: bytes) -> None:
        nonlocal total
        hasher.update(chunk)
        total += len(chunk)

    header: bytes | None = None
    columns: dict[bytes, int] = {}
    kept: list[bytes] = []
    want_state = state_fips.encode("ascii")
    for line in stream_lines(url, on_chunk=on_chunk):
        line = line.rstrip(b"\r")
        if header is None:
            # The published file begins with a byte-order mark.
            header = line.lstrip(b"\xef\xbb\xbf")
            columns = {name: i for i, name in enumerate(header.split(b"|"))}
            missing = [c for c in (b"SUMLEVEL", b"COMPONENT", b"STATE", b"GEO_ID",
                                   b"NAME") if c not in columns]
            if missing:
                raise RedactedError(
                    f"unexpected geography file header for {release.release_id}: "
                    f"missing {missing}; the published format changed and the "
                    "reader must be reviewed")
            continue
        fields = line.split(b"|")
        if len(fields) < len(columns):
            continue
        if (fields[columns[b"STATE"]] == want_state
                and fields[columns[b"COMPONENT"]] == b"00"
                and fields[columns[b"SUMLEVEL"]] in ROSTER_SUMMARY_LEVELS):
            kept.append(line)

    if header is None:
        raise RedactedError(f"empty geography file for {release.release_id}")

    rel = roster_cache_rel_path(release, state_fips)
    path = repo_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"\n".join([header] + kept) + b"\n"
    path.write_bytes(body)

    manifest.add(provenance.RetrievalRecord(
        artifact_id=f"roster:{release.release_id}:state{state_fips}",
        provider=release.provider,
        kind="geography_roster",
        source_url=url,
        request={
            "method": "GET",
            "transport": "table-based Summary File documentation",
            "release": release.release_id,
            "period_label": release.period_label,
            "filter": {"STATE": state_fips, "COMPONENT": "00",
                       "SUMLEVEL": sorted(k.decode() for k in ROSTER_SUMMARY_LEVELS)},
        },
        retrieved_at=provenance.utc_now(),
        http_status=200,
        content_bytes=len(body),
        sha256=provenance.sha256_bytes(body),
        cache_path=rel,
        upstream_full_sha256=hasher.hexdigest(),
        upstream_full_bytes=total,
        notes=[
            f"Retained {len(kept)} rows: the state, county and census tract "
            f"geographies this release publishes for state {state_fips}.",
            "Rows are verbatim; the digest of the complete upstream file is "
            "recorded so the download can be re-verified.",
        ],
    ))
    if progress:
        progress(f"geography roster: kept {len(kept)} rows from "
                 f"{total / 1e6:.1f} MB upstream")
    return path


def read_roster(path: Path) -> dict[str, dict[str, str]]:
    """``{GEO_ID: {"level": ..., "name": ...}}`` from a cached roster."""
    out: dict[str, dict[str, str]] = {}
    lines = path.read_bytes().split(b"\n")
    columns = {name: i for i, name in enumerate(lines[0].split(b"|"))}
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split(b"|")
        level = ROSTER_SUMMARY_LEVELS.get(fields[columns[b"SUMLEVEL"]])
        if level is None:
            continue
        geo_id = fields[columns[b"GEO_ID"]].decode("ascii")
        out[geo_id] = {"level": level,
                       "name": fields[columns[b"NAME"]].decode("utf-8", "replace")}
    return out
