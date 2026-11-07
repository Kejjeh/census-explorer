"""Explicit retrieval of the hospital registry sources (Hospital Explorer phase 1).

Nothing runs at import time. ``fetch_all`` is called only by
``python -m census_explorer.cli hospitals fetch``.

Each run writes a new, immutable directory ``data/raw/hospitals/<stamp>/``
and a manifest ``data/manifests/hospitals-<stamp>.json``. Files are written
into a staging directory first; the directory is renamed into place and the
manifest saved only after every check below passed, so a failed run leaves
neither a manifest nor a half-filled cache behind.

Checks, all fatal:

* **Schema drift.** The CSV header must equal the header in
  ``config/hospitals.json`` exactly: same names, same order.
* **Truncation.** The number of data rows in each complete CSV export must
  equal the provider's own count API. For CMS Hospital General Information
  and the NYS General file the New York / hospital-family subset is also
  counted by the provider and compared with the local filter.
* **Safe sources.** Download addresses read from CMS metadata must be https
  on data.cms.gov; documents must look like what they claim to be.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .. import provenance
from ..http_client import fetch as _default_fetch
from ..redact import RedactedError

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "hospitals.json"
RULES_PATH = CONFIG_PATH.with_name("hospital_rules.json")
RAW_ROOT = Path("data/raw/hospitals")
MANIFEST_PREFIX = "hospitals-"


class SourceCheckFailed(RedactedError):
    """A source failed a schema, count or safety check; nothing was kept."""


def load_config(path: Path | None = None) -> dict:
    """The retrieval configuration: sources, documents, hospital-family types."""
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_rules(path: Path | None = None) -> dict:
    """The transformation rules: aliases, bounds, certification notes, links."""
    with open(path or RULES_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def canonical_sha256(doc: Any) -> str:
    """SHA-256 of canonical JSON, so the same content hashes the same on every
    platform whatever the file's line endings or key order."""
    return provenance.sha256_bytes(
        json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


# -- parsing helpers shared with the offline build -------------------------

def decode_csv(body: bytes) -> tuple[list[str], list[list[str]], bool]:
    """Return (header, rows, had_bom). Blank physical lines are not rows."""
    had_bom = body.startswith(b"\xef\xbb\xbf")
    text = body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise SourceCheckFailed("empty CSV: no header row") from None
    rows = [r for r in reader if r and any(cell != "" for cell in r)]
    bad = [i for i, r in enumerate(rows) if len(r) != len(header)]
    if bad:
        raise SourceCheckFailed(
            f"{len(bad)} CSV row(s) have a different number of fields from the "
            f"header (first at data row {bad[0] + 1}); the export is malformed")
    return header, rows, had_bom


def check_header(source_key: str, header: list[str], expected: list[str]) -> None:
    if header == expected:
        return
    missing = [h for h in expected if h not in header]
    extra = [h for h in header if h not in expected]
    detail = []
    if missing:
        detail.append(f"missing {missing}")
    if extra:
        detail.append(f"unexpected {extra}")
    if not missing and not extra:
        detail.append("same columns in a different order")
    raise SourceCheckFailed(
        f"schema drift in {source_key}: " + "; ".join(detail)
        + ". Review the provider's data dictionary and update "
        "config/hospitals.json deliberately; nothing was kept.")


def _json(body: bytes, what: str) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SourceCheckFailed(f"{what} did not return JSON") from None


def socrata_count(body: bytes, what: str) -> int:
    doc = _json(body, what)
    if (not isinstance(doc, list) or len(doc) != 1 or not isinstance(doc[0], dict)
            or len(doc[0]) != 1):
        raise SourceCheckFailed(f"{what}: unexpected count response")
    value = next(iter(doc[0].values()))
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SourceCheckFailed(f"{what}: count is not an integer") from None


def dkan_count(body: bytes, what: str) -> int:
    doc = _json(body, what)
    if not isinstance(doc, dict) or not isinstance(doc.get("count"), int):
        raise SourceCheckFailed(f"{what}: unexpected count response")
    return doc["count"]


def _safe_cms_url(url: Any, what: str) -> str:
    if not isinstance(url, str):
        raise SourceCheckFailed(f"{what}: CMS metadata gave no download address")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "data.cms.gov":
        raise SourceCheckFailed(
            f"{what}: download address is not https on data.cms.gov ({parts.hostname})")
    return url


def soql_in(field: str, values: list[str]) -> str:
    quoted = ",".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{field} in({quoted})"


def family_types(cfg: dict) -> list[str]:
    fam = cfg["hospital_family"]
    return list(fam["main_site_types"]) + list(fam["hospital_operated_types"])


# -- retrieval --------------------------------------------------------------

class _Run:
    def __init__(self, repo_root: Path, stamp: str, fetcher: Callable, log):
        self.repo_root = repo_root
        self.stamp = stamp
        self.fetcher = fetcher
        self.log = log
        self.final_rel = RAW_ROOT / stamp
        self.final = repo_root / self.final_rel
        self.staging = repo_root / RAW_ROOT / f".{stamp}.partial"
        self.records: list[provenance.RetrievalRecord] = []

    def get(self, url: str):
        resp = self.fetcher(url)
        if resp.status != 200:
            raise SourceCheckFailed(f"HTTP {resp.status} from {resp.url_sanitized}")
        return resp

    def keep(self, artifact_id: str, provider: str, kind: str, name: str, resp,
             notes: list[str], request: dict | None = None) -> None:
        path = self.staging / name
        path.write_bytes(resp.body)
        self.records.append(provenance.RetrievalRecord(
            artifact_id=artifact_id, provider=provider, kind=kind,
            source_url=resp.url_sanitized, request=request or {"method": "GET"},
            retrieved_at=provenance.utc_now(), http_status=resp.status,
            content_bytes=len(resp.body), sha256=provenance.sha256_bytes(resp.body),
            cache_path=(self.final_rel / name).as_posix(), notes=notes))


def fetch_all(repo_root: Path, *, fetcher: Callable | None = None,
              config: dict | None = None, stamp: str | None = None,
              log=print) -> provenance.Manifest:
    """Retrieve every hospital source, check it, and write cache + manifest.

    Only the real network fetcher produces a ``live`` retrieval. Any other
    fetcher (a test double) produces ``fixture``, whatever it serves: a
    retrieval is never called live because it went through the same code.
    """
    repo_root = Path(repo_root)
    data_mode = "live" if fetcher is None else "fixture"
    fetcher = fetcher or _default_fetch
    cfg = config or load_config()
    stamp = stamp or provenance.utc_now().replace(":", "").replace("-", "")
    run = _Run(repo_root, stamp, fetcher, log)
    if run.final.exists():
        raise SourceCheckFailed(
            f"{run.final_rel.as_posix()} already exists; raw retrievals are "
            "immutable and are never overwritten")
    if run.staging.exists():
        shutil.rmtree(run.staging)
    run.staging.mkdir(parents=True)
    inputs: dict[str, Any] = {"sources": {}, "documents": {}}
    try:
        for key, src in cfg["sources"].items():
            if src["kind"] == "cms_provider_data":
                inputs["sources"][key] = _fetch_cms(run, key, src, cfg)
            else:
                inputs["sources"][key] = _fetch_socrata(run, key, src, cfg)
        for key, doc in cfg["documents"].items():
            resp = run.get(doc["url"])
            # The portal labels this download application/octet-stream, so the
            # file's own signature is what is checked.
            if not resp.body.startswith(b"%PDF"):
                raise SourceCheckFailed(f"{key}: expected a PDF document")
            run.keep(f"terms:{key}", "New York State", "reference", f"{key}.pdf", resp,
                     [doc["title"]])
            inputs["documents"][key] = {"title": doc["title"], "url": doc["url"],
                                        "bytes": len(resp.body)}
    except BaseException:
        shutil.rmtree(run.staging, ignore_errors=True)
        raise

    run.staging.rename(run.final)
    manifest = provenance.Manifest.new(
        f"{MANIFEST_PREFIX}{stamp}",
        "Hospital Explorer phase 1 sources: CMS Hospital General Information and "
        "Footnote Crosswalk, NYSDOH HFIS General and Certification, NYS ITS "
        "Locality Hierarchy, and their documentation.",
        data_mode, repo_root)
    for rec in run.records:
        manifest.add(rec)
    # The retrieval configuration this cache was checked against. A build
    # refuses the retrieval if the configuration has changed since.
    inputs["retrieval_config_sha256"] = canonical_sha256(cfg)
    manifest.inputs = inputs
    path = repo_root / "data" / "manifests" / f"{manifest.manifest_id}.json"
    manifest.save(path)
    log(f"manifest written: {path.relative_to(repo_root).as_posix()}")
    return manifest


def _fetch_cms(run: _Run, key: str, src: dict, cfg: dict) -> dict:
    provider = src["publisher"]
    meta_resp = run.get(src["metastore_url"])
    meta = _json(meta_resp.body, f"{key} metadata")
    run.keep(f"{key}:metadata", provider, "metadata", f"{key}.metadata.json", meta_resp,
             [f"CMS Provider Data Catalog metastore record for {src['dataset_id']}."])
    if meta.get("identifier") != src["dataset_id"]:
        raise SourceCheckFailed(f"{key}: metadata is for {meta.get('identifier')!r}")
    dists = meta.get("distribution") or []
    if len(dists) != 1:
        raise SourceCheckFailed(f"{key}: expected one distribution, found {len(dists)}")
    data_url = _safe_cms_url(dists[0].get("downloadURL"), key)

    resp = run.get(data_url)
    header, rows, had_bom = decode_csv(resp.body)
    check_header(key, header, src["expected_header"])
    api_count = dkan_count(run.get(src["count_url"]).body, f"{key} count")
    if api_count != len(rows):
        raise SourceCheckFailed(
            f"{key}: the CSV holds {len(rows)} rows but the CMS datastore reports "
            f"{api_count}; the download is incomplete or out of step")
    out = {
        "dataset_id": src["dataset_id"], "title": meta.get("title"),
        "publisher": provider, "landing_page": meta.get("landingPage"),
        "modified": meta.get("modified"), "released": meta.get("released"),
        "issued": meta.get("issued"), "access_level": meta.get("accessLevel"),
        "license": meta.get("license"), "download_url": data_url,
        "rows": len(rows), "api_count": api_count, "had_bom": had_bom,
    }
    if "ny_count_url" in src:
        state_col = header.index("State")
        local_ny = sum(1 for r in rows if r[state_col] == "NY")
        api_ny = dkan_count(run.get(src["ny_count_url"]).body, f"{key} NY count")
        if api_ny != local_ny:
            raise SourceCheckFailed(
                f"{key}: {local_ny} New York rows locally, {api_ny} by the CMS datastore")
        out.update(ny_rows=local_ny, ny_api_count=api_ny)
    run.keep(f"{key}:data", provider, "observations", f"{key}.csv", resp,
             [f"Complete CSV distribution of {src['dataset_id']}; {len(rows)} rows, "
              f"equal to the datastore count."])
    if src.get("archive_dictionary"):
        dict_url = _safe_cms_url(dists[0].get("describedBy"), f"{key} dictionary")
        d = run.get(dict_url)
        if not d.body.startswith(b"%PDF"):
            raise SourceCheckFailed(f"{key}: data dictionary is not a PDF")
        run.keep(f"{key}:dictionary", provider, "reference", f"{key}.dictionary.pdf", d,
                 ["CMS hospital downloadable database data dictionary."])
        out["dictionary_url"] = dict_url
    return out


def _fetch_socrata(run: _Run, key: str, src: dict, cfg: dict) -> dict:
    provider = src["publisher"]
    base = f"https://{src['domain']}"
    ident = src["dataset_id"]
    meta_resp = run.get(f"{base}/api/views/{ident}.json")
    meta = _json(meta_resp.body, f"{key} metadata")
    if meta.get("id") != ident:
        raise SourceCheckFailed(f"{key}: metadata is for {meta.get('id')!r}")
    run.keep(f"{key}:metadata", provider, "metadata", f"{key}.metadata.json", meta_resp,
             [f"Socrata view metadata for {ident}: column descriptions, "
              "attribution, license and update times."])
    resp = run.get(f"{base}/api/views/{ident}/rows.csv?accessType=DOWNLOAD")
    header, rows, had_bom = decode_csv(resp.body)
    check_header(key, header, src["expected_header"])
    q = urllib.parse.urlencode({"$select": "count(*)"})
    api_count = socrata_count(run.get(f"{base}/resource/{ident}.json?{q}").body,
                              f"{key} count")
    if api_count != len(rows):
        raise SourceCheckFailed(
            f"{key}: the export holds {len(rows)} rows but the API counts "
            f"{api_count}; the download is incomplete or out of step")
    out = {
        "dataset_id": ident, "title": meta.get("name"), "publisher": provider,
        "attribution": meta.get("attribution"),
        "license": (meta.get("license") or {}).get("name") if isinstance(meta.get("license"), dict) else meta.get("license"),
        "landing_page": src["landing_page"],
        "rows_updated_at": meta.get("rowsUpdatedAt"),
        "view_last_modified": meta.get("viewLastModified"),
        "rows": len(rows), "api_count": api_count, "had_bom": had_bom,
    }
    if key == "nys_general":
        types = family_types(cfg)
        col = header.index("Description")
        local = sum(1 for r in rows if r[col] in types)
        q = urllib.parse.urlencode({"$select": "count(*)",
                                    "$where": soql_in(src["filter_field"], types)})
        api = socrata_count(run.get(f"{base}/resource/{ident}.json?{q}").body,
                            f"{key} hospital-family count")
        if api != local:
            raise SourceCheckFailed(
                f"{key}: {local} hospital-family rows locally, {api} by the API")
        out.update(family_rows=local, family_api_count=api)
    run.keep(f"{key}:data", provider, "observations", f"{key}.csv", resp,
             [f"Complete CSV export of {ident}; {len(rows)} rows, equal to the API count."])
    return out
