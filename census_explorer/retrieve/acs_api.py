"""Retrieve ACS estimates from the Census Data API (requires an API key).

The key is read from the local ``CENSUS_API_KEY`` environment variable at the
moment of the call and is never stored, logged, echoed into a manifest, sent to
the browser, or written into a cache file.  The sanitized URL recorded in
provenance has the key parameter removed entirely.

Request your own key at https://api.census.gov/data/key_signup.html.  Do not
paste a key into a chat window or commit it.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import provenance
from ..config import Release
from ..http_client import build_url, fetch, sanitize_url
from ..redact import RedactedError

#: Maximum variables the Census API accepts in one ``get=`` list.
MAX_VARS_PER_REQUEST = 50


class MissingCredential(RuntimeError):
    """No Census API key is configured in the local environment."""


def api_key(environ: dict | None = None) -> str:
    env = os.environ if environ is None else environ
    key = (env.get("CENSUS_API_KEY") or "").strip()
    if not key:
        raise MissingCredential(
            "CENSUS_API_KEY is not set in this environment. Set it locally "
            "(PowerShell: $env:CENSUS_API_KEY = '<your key>') or use the keyless "
            "Summary File transport instead: --transport summary-file"
        )
    return key


def has_api_key(environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    return bool((env.get("CENSUS_API_KEY") or "").strip())


def geography_params(level: str, state_fips: str, county_fips: str | None = None) -> dict:
    if level == "county":
        return {"for": "county:*", "in": f"state:{state_fips}"}
    if level == "tract":
        if not county_fips:
            raise ValueError("tract requests need a county")
        return {"for": "tract:*", "in": f"state:{state_fips} county:{county_fips}"}
    raise ValueError(f"unsupported geography level {level!r}")


def chunk_variables(variables: list[str]) -> list[list[str]]:
    """Split the variable list so each request stays inside the API limit.

    ``GEO_ID`` and ``NAME`` are re-requested in every chunk so each response is
    independently joinable.
    """
    payload = [v for v in variables if v not in ("GEO_ID", "NAME")]
    room = MAX_VARS_PER_REQUEST - 2
    return [["GEO_ID", "NAME"] + payload[i:i + room] for i in range(0, len(payload), room)]


def fetch_variables(repo_root: Path, release: Release, table: str, level: str,
                    variables: list[str], geo_params: dict,
                    manifest: provenance.Manifest,
                    environ: dict | None = None,
                    label: str = "") -> list[Path]:
    """Fetch a variable set for one geography selection; cache responses verbatim."""
    key = api_key(environ)
    paths: list[Path] = []
    for index, chunk in enumerate(chunk_variables(variables)):
        params = {"get": ",".join(chunk)}
        params.update(geo_params)
        sanitized = sanitize_url(build_url(release.api_base, params))
        url = build_url(release.api_base, {**params, "key": key})
        resp = fetch(url)
        if not resp.body.lstrip().startswith(b"["):
            raise RedactedError(
                f"Census API did not return a JSON array for {sanitized}; "
                "the response was not cached"
            )
        rel = (f"data/raw/acs/{release.release_id}/api/"
               f"{table}_{level}{('_' + label) if label else ''}_{index:02d}.json")
        path = repo_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.body)
        manifest.add(provenance.RetrievalRecord(
            artifact_id=f"observations:{release.release_id}:{table}:api:{level}:{label}:{index}",
            provider=release.provider,
            kind="observations",
            source_url=sanitized,
            request={
                "method": "GET",
                "transport": "Census Data API",
                "release": release.release_id,
                "period_label": release.period_label,
                "table": table,
                "level": level,
                "get": chunk,
                "geography": geo_params,
                "credential": "CENSUS_API_KEY supplied from the local environment; "
                              "not recorded here",
            },
            retrieved_at=provenance.utc_now(),
            http_status=resp.status,
            content_bytes=len(resp.body),
            sha256=provenance.sha256_bytes(resp.body),
            cache_path=rel,
            notes=["Response body cached verbatim."],
        ))
        paths.append(path)
    return paths
