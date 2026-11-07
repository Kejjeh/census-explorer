"""Download Census cartographic boundary files of the matching vintage.

The boundary release is part of the dataset key, not an afterthought: the
2019-2023 ACS five-year tables are published against 2023 geography, so the
GENZ2023 cartographic files are the ones that may be joined to them.  Using a
different vintage is a silent correctness failure, so the vintage travels in
the manifest and in every export.
"""

from __future__ import annotations

from pathlib import Path

from .. import provenance
from ..config import Release
from ..http_client import fetch

BASE = "https://www2.census.gov/geo/tiger"


def county_url(release: Release) -> str:
    year = release.boundary_release.replace("GENZ", "")
    return f"{BASE}/{release.boundary_release}/shp/cb_{year}_us_county_500k.zip"


def tract_url(release: Release, state_fips: str) -> str:
    year = release.boundary_release.replace("GENZ", "")
    return f"{BASE}/{release.boundary_release}/shp/cb_{year}_{state_fips}_tract_500k.zip"


def cache_rel_path(release: Release, level: str, state_fips: str | None = None) -> str:
    year = release.boundary_release.replace("GENZ", "")
    if level == "county":
        name = f"cb_{year}_us_county_500k.zip"
    else:
        name = f"cb_{year}_{state_fips}_tract_500k.zip"
    return f"data/raw/geo/{release.boundary_release}/{name}"


def fetch_boundaries(repo_root: Path, release: Release, level: str,
                     state_fips: str, manifest: provenance.Manifest) -> Path:
    url = county_url(release) if level == "county" else tract_url(release, state_fips)
    rel = cache_rel_path(release, level, state_fips)
    path = repo_root / rel
    resp = fetch(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.body)
    manifest.add(provenance.RetrievalRecord(
        artifact_id=f"geography:{release.boundary_release}:{level}",
        provider="US Census Bureau",
        kind="geography",
        source_url=resp.url_sanitized,
        request={"method": "GET", "level": level, "state_fips": state_fips,
                 "boundary_release": release.boundary_release},
        retrieved_at=provenance.utc_now(),
        http_status=resp.status,
        content_bytes=len(resp.body),
        sha256=provenance.sha256_bytes(resp.body),
        cache_path=rel,
        notes=[
            f"Cartographic boundary file (1:500,000), {release.boundary_release}.",
            "Boundary files carry identifiers and geometry only; they contain no "
            "demographic observations.",
        ],
    ))
    return path
