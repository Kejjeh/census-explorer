"""The local service.

Bound to the loopback interface, single user, no authentication because nothing
outside the machine can reach it.  Two guarantees matter:

* The process switches network access off for itself at start-up, so a bug in a
  request handler cannot turn a page render into a download.
* No credential is read, held or served.  The browser never sees a key because
  the service never has one.
"""

from __future__ import annotations

import json
import mimetypes
import posixpath
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import (compare as compare_mod, config as config_mod, exports, figures,
               http_client, metadata as metadata_mod, projects as projects_mod,
               provenance)
from .redact import redact, redact_structure

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 1 << 20


class ServiceState:
    """Loads processed datasets from disk.  Never fetches."""

    def __init__(self, repo_root: Path, data_dir: str = "data/processed"):
        self.repo_root = repo_root
        self.data_dir = data_dir
        self.config = config_mod.load()
        self._lock = threading.RLock()  # re-entrant: values() loads the dataset it guards
        self._datasets: dict[str, dict] = {}
        self._values: dict[tuple[str, str], dict] = {}

    # -- loading ---------------------------------------------------------
    def dataset_path(self, release_id: str) -> Path:
        return self.repo_root / self.data_dir / release_id / "dataset.json"

    def available_releases(self) -> list[str]:
        root = self.repo_root / self.data_dir
        if not root.exists():
            return []
        return sorted(p.name for p in root.iterdir()
                      if (p / "dataset.json").exists())

    def dataset(self, release_id: str) -> dict:
        with self._lock:
            if release_id not in self._datasets:
                path = self.dataset_path(release_id)
                if not path.exists():
                    raise FileNotFoundError(
                        f"no built dataset for '{release_id}'. Build it first: "
                        f"python -m census_explorer.cli build --release {release_id}"
                    )
                with open(path, "r", encoding="utf-8") as fh:
                    self._datasets[release_id] = json.load(fh)
            return self._datasets[release_id]

    def values(self, release_id: str, measure_id: str) -> dict[str, dict]:
        key = (release_id, measure_id)
        with self._lock:
            if key not in self._values:
                ds = self.dataset(release_id)
                rel = ds["value_files"].get(measure_id)
                if not rel:
                    raise KeyError(f"measure '{measure_id}' is not in this dataset")
                path = self.repo_root / self.data_dir / release_id / rel
                with open(path, "r", encoding="utf-8") as fh:
                    self._values[key] = json.load(fh)["values"]
            return self._values[key]

    def geography(self, release_id: str, level: str) -> dict:
        path = self.repo_root / self.data_dir / release_id / f"geography_{level}.geojson"
        if not path.exists():
            raise FileNotFoundError(
                f"no {level} boundaries built for '{release_id}'. "
                "Retrieve them with: fetch geography"
            )
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def data_mode(self) -> str:
        modes = {self.dataset(r).get("data_mode", "live") for r in self.available_releases()}
        if not modes:
            return "none"
        return "fixture" if "fixture" in modes else "live"

    def areas(self, release_id: str) -> dict[str, dict]:
        return {a["geoid"]: a for a in self.dataset(release_id)["areas"]}

    def release(self, release_id: str) -> config_mod.Release:
        """Resolve a release from the built dataset, falling back to config.

        Fixture datasets describe a release that is deliberately absent from the
        project configuration, so the dataset is the authority here.
        """
        try:
            return self.config.release(release_id)
        except config_mod.ConfigError:
            r = self.dataset(release_id)["release"]
            return config_mod.Release(
                release_id=r["release_id"], provider=r["provider"], dataset=r["dataset"],
                vintage=r["vintage"], period_start=r["period_start"],
                period_end=r["period_end"], period_label=r["period_label"],
                product_label=r["product_label"], boundary_release=r["boundary_release"],
                geography_vintage=r["geography_vintage"], api_base="", summary_file_base="",
                citation=r["citation"],
            )

    def measure(self, release_id: str, measure_id: str) -> dict:
        for m in self.dataset(release_id)["measures"]:
            if m["measure_id"] == measure_id:
                return m
        raise KeyError(f"unknown measure '{measure_id}'")


class Handler(BaseHTTPRequestHandler):
    server_version = "CensusExplorer/0.1"
    state: ServiceState

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # redact anything logged
        print(redact("  %s - %s" % (self.address_string(), fmt % args)))

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This service is local-only and must not be embedded or framed remotely.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(redact_structure(obj), separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": redact(message)}, status)

    def _guard_host(self) -> bool:
        """Reject requests that did not arrive addressed to the loopback name."""
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        if host in ("127.0.0.1", "localhost", "::1", ""):
            return True
        self._error(403, "this service only answers requests addressed to localhost")
        return False

    # -- routing ---------------------------------------------------------
    def do_GET(self) -> None:
        if not self._guard_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                return self._api_get(path, query)
            return self._static(path)
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except KeyError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if not self._guard_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._error(413, "request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return self._error(400, "request body is not JSON")
        try:
            if parsed.path == "/api/projects":
                return self._save_project(payload)
            if parsed.path == "/api/projects/delete":
                ok = projects_mod.delete(self.state.repo_root, payload.get("project_id", ""))
                return self._json({"deleted": ok})
            if parsed.path == "/api/export":
                return self._export(payload)
            self._error(404, f"no such endpoint: {parsed.path}")
        except (ValueError, KeyError, FileNotFoundError) as exc:
            self._error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            self._error(500, f"{type(exc).__name__}: {exc}")

    # -- static ----------------------------------------------------------
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else posixpath.normpath(path.lstrip("/"))
        if rel.startswith("..") or Path(rel).is_absolute():
            return self._error(403, "forbidden path")
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._error(404, f"not found: {path}")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- API -------------------------------------------------------------
    def _api_get(self, path: str, query: dict[str, list[str]]) -> None:
        st = self.state
        one = lambda k, default=None: (query.get(k) or [default])[0]

        if path == "/api/status":
            releases = st.available_releases()
            return self._json({
                "data_mode": st.data_mode(),
                "releases": [st.dataset(r)["release"] for r in releases],
                "default_release": st.config.raw["explorer"]["default_release"],
                "comparison_release": st.config.raw["explorer"]["comparison_release"],
                "code_revision": provenance.code_revision(st.repo_root),
                "offline": http_client.is_offline(),
                "annotation_reference": _annotation_provenance(),
            })

        if path == "/api/dataset":
            release = one("release") or st.config.raw["explorer"]["default_release"]
            ds = dict(st.dataset(release))
            ds.pop("value_files", None)
            return self._json(ds)

        if path == "/api/values":
            release = one("release")
            measure = one("measure")
            if not release or not measure:
                raise ValueError("release and measure are required")
            return self._json({
                "release_id": release,
                "measure_id": measure,
                "period_label": st.dataset(release)["release"]["period_label"],
                "values": st.values(release, measure),
            })

        if path == "/api/geography":
            release = one("release")
            level = one("level", "county")
            if not release:
                raise ValueError("release is required")
            return self._json(st.geography(release, level))

        if path == "/api/compare":
            return self._json(_compare(st, one("a"), one("b"), one("level", "county"),
                                       one("measure")))

        if path == "/api/projects":
            return self._json({"projects": projects_mod.listing(st.repo_root)})

        if path == "/api/project":
            pid = one("id")
            if not pid:
                raise ValueError("id is required")
            return self._json(projects_mod.load(st.repo_root, pid).to_json())

        if path == "/api/figure":
            return self._figure(query)

        raise KeyError(f"no such endpoint: {path}")

    # -- figure ----------------------------------------------------------
    def _figure(self, query: dict[str, list[str]]) -> None:
        st = self.state
        one = lambda k, default=None: (query.get(k) or [default])[0]
        kind = one("kind", "map")
        release = one("release") or st.config.raw["explorer"]["default_release"]
        level = one("level", "county")
        measure_id = one("measure")
        compare_with = one("compare")
        if not measure_id:
            raise ValueError("measure is required")
        svg = build_figure(st, kind, release, level, measure_id, compare_with)
        self._send(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")

    # -- projects & export ------------------------------------------------
    def _save_project(self, payload: dict) -> None:
        st = self.state
        ds = st.dataset(payload["release_id"])
        project = projects_mod.SavedProject(
            project_id=payload["project_id"],
            title=payload.get("title") or payload["project_id"],
            release_id=payload["release_id"],
            level=payload.get("level", "county"),
            measure_id=payload["measure_id"],
            areas=list(payload.get("areas") or []),
            comparison_release_id=payload.get("comparison_release_id") or None,
            classes=int(payload.get("classes") or 5),
            cut_points=payload.get("cut_points"),
            notes=payload.get("notes", ""),
            manifest_ids=list(ds.get("manifest_ids") or [ds.get("manifest_id")]) + (
                list(st.dataset(payload["comparison_release_id"]).get("manifest_ids")
                     or [st.dataset(payload["comparison_release_id"]).get("manifest_id")])
                if payload.get("comparison_release_id") else []
            ),
        )
        path = projects_mod.save(st.repo_root, project)
        self._json({"saved": project.project_id,
                    "path": str(path.relative_to(st.repo_root))})

    def _export(self, payload: dict) -> None:
        st = self.state
        result = run_export(st, payload)
        self._json(result)


def _annotation_provenance() -> dict:
    from .sentinels import reference_provenance
    return reference_provenance()


def _compare(st: ServiceState, a: str | None, b: str | None, level: str,
             measure_id: str | None) -> dict:
    if not a or not b:
        raise ValueError("a and b release identifiers are required")
    release_a = st.release(a)
    release_b = st.release(b)
    geo_a = {g for g, x in st.areas(a).items() if x["level"] == level}
    geo_b = {g for g, x in st.areas(b).items() if x["level"] == level}
    measure = st.config.measures.get(measure_id) if measure_id else None
    meta_a = meta_b = None
    if measure is not None and a in st.config.releases and b in st.config.releases:
        tables = measure.tables
        meta_a = metadata_mod.load_release_metadata(st.repo_root, release_a, tables)
        meta_b = metadata_mod.load_release_metadata(st.repo_root, release_b, tables)
    report = compare_mod.check(release_a, release_b, level, geo_a, geo_b,
                               meta_a, meta_b, measure)
    out = report.to_json()
    if report.allowed and measure is not None:
        pooled = []
        for rid in (a, b):
            for g, v in st.values(rid, measure.measure_id).items():
                if g in (geo_a & geo_b) and v.get("es") == "ok":
                    pooled.append(v["e"])
        out["shared_cut_points"] = compare_mod.shared_cut_points(pooled, 5)
        out["shared_cut_points_note"] = (
            "Both panels use these breaks. Independent legends on each side can "
            "manufacture the appearance of change."
        )
    return out


def build_figure(st: ServiceState, kind: str, release_id: str, level: str,
                 measure_id: str, compare_with: str | None = None) -> str:
    ds = st.dataset(release_id)
    release = st.release(release_id)
    measure = st.config.measures[measure_id]
    mjson = st.measure(release_id, measure_id)
    values = st.values(release_id, measure_id)
    areas = st.areas(release_id)
    data_mode = ds.get("data_mode", "live")

    sources = [
        f"Source: {release.citation}",
        f"Tables: {', '.join(measure.tables)}. Universe: {mjson['universe_published'][0]}",
        f"Geography: {release.geography_vintage}. Measure: {measure.definition_note}",
    ]

    compatibility = None
    if compare_with:
        compatibility = _compare(st, release_id, compare_with, level, measure_id)
        if not compatibility["allowed"]:
            raise ValueError(
                "this comparison is blocked: " + "; ".join(compatibility["blocking"])
            )

    if kind == "map":
        geo = st.geography(release_id, level)
        usable = [v["e"] for g, v in values.items()
                  if v.get("es") == "ok" and areas.get(g, {}).get("level") == level]
        cuts = figures.quantile_cuts(usable, 5)
        if compatibility:
            cuts = compatibility.get("shared_cut_points") or cuts
        return figures.choropleth_svg(
            features=geo["features"], values=values,
            title=measure.label,
            subtitle=f"{release.period_label} · {level} · unit: "
                     f"{'percent' if measure.unit == 'percent' else 'persons'}",
            unit=measure.unit, cuts=cuts, source_lines=sources,
            data_mode=data_mode, panel_label=release.period_label,
        )

    if kind == "chart":
        series_releases = [release_id] + ([compare_with] if compare_with else [])
        series_labels = [st.release(r).period_label for r in series_releases]
        selected = [g for g, a in areas.items() if a["level"] == level]
        if level == "tract":
            # A readable chart needs a bounded number of rows.
            ranked = sorted(
                (g for g in selected if values.get(g, {}).get("es") == "ok"),
                key=lambda g: values[g]["e"], reverse=True,
            )
            selected = ranked[:25]
        rows = []
        for g in sorted(selected, key=lambda g: areas[g]["name"]):
            row_values = []
            for rid in series_releases:
                v = st.values(rid, measure_id).get(g, {})
                row_values.append({
                    "e": v.get("e") if v.get("es") == "ok" else None,
                    "m": v.get("m") if v.get("ms") == "ok" else None,
                    "note": v.get("er") or "no usable estimate",
                })
            rows.append({"label": areas[g]["name"], "values": row_values})
        disclosures = list(compatibility["disclosures"]) if compatibility else []
        return figures.group_chart_svg(
            rows=rows, title=measure.label,
            subtitle=" vs ".join(series_labels) + f" · {level} · "
                     f"bars show the estimate, whiskers the 90% margin of error",
            unit=measure.unit, source_lines=sources, series_labels=series_labels,
            data_mode=data_mode, disclosures=disclosures,
        )

    raise ValueError(f"unknown figure kind {kind!r}")


def run_export(st: ServiceState, payload: dict) -> dict:
    release_ids = [payload.get("release_id")] + (
        [payload["comparison_release_id"]] if payload.get("comparison_release_id") else []
    )
    release_ids = [r for r in release_ids if r]
    if not release_ids:
        raise ValueError("release_id is required")
    measure_ids = payload.get("measure_ids") or ([payload["measure_id"]]
                                                 if payload.get("measure_id") else [])
    if not measure_ids:
        raise ValueError("measure_id or measure_ids is required")
    level = payload.get("level", "county")
    wanted_areas = set(payload.get("areas") or [])

    releases = [st.release(r) for r in release_ids]
    measures = [st.config.measures[m] for m in measure_ids]
    areas = {g: a for g, a in st.areas(release_ids[0]).items()
             if a["level"] == level and (not wanted_areas or g in wanted_areas)}
    values: dict[tuple[str, str], dict] = {}
    for rid in release_ids:
        for mid in measure_ids:
            values[(rid, mid)] = {
                g: v for g, v in st.values(rid, mid).items() if g in areas
            }

    compatibility = None
    if len(release_ids) > 1:
        compatibility = _compare(st, release_ids[0], release_ids[1], level, measure_ids[0])
        if not compatibility["allowed"]:
            raise ValueError("this comparison is blocked: "
                             + "; ".join(compatibility["blocking"]))

    project = None
    if payload.get("project_id"):
        try:
            project = projects_mod.load(st.repo_root, payload["project_id"])
        except projects_mod.ProjectError:
            project = None

    datasets = [st.dataset(r) for r in release_ids]
    data_mode = "fixture" if any(d.get("data_mode") == "fixture" for d in datasets) else "live"
    csv_text = exports.build_csv(releases, areas, measures, values)
    prov = exports.build_provenance(st.repo_root, st.config, releases, measures,
                                    project, datasets, compatibility, data_mode)
    svg = None
    if payload.get("include_figure", True):
        svg = build_figure(st, payload.get("figure_kind", "map"), release_ids[0], level,
                           measure_ids[0],
                           release_ids[1] if len(release_ids) > 1 else None)

    stamp = provenance.utc_now().replace(":", "").replace("-", "")
    name = payload.get("name") or f"{measure_ids[0]}-{level}-{release_ids[0]}"
    out_dir = st.repo_root / "artifacts" / f"{name}-{stamp}"
    written = exports.write_bundle(out_dir, csv_text, prov, svg)
    return {
        "export_dir": str(out_dir.relative_to(st.repo_root)),
        "files": [str(p.relative_to(st.repo_root)) for p in written],
        "rows": csv_text.count("\n") - 1,
        "data_mode": data_mode,
        "comparison": compatibility,
    }


def serve(repo_root: Path, host: str = "127.0.0.1", port: int = 8765,
          data_dir: str = "data/processed", log=print) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(
            f"refusing to bind to {host!r}: this service is local-only by design"
        )
    # The service must never fetch. Turn the network off for this process.
    http_client.set_offline(True)
    state = ServiceState(repo_root, data_dir)
    releases = state.available_releases()
    if not releases:
        raise FileNotFoundError(
            "no built dataset found. Run: python -m census_explorer.cli build"
        )
    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)
    log(f"Census Explorer service on http://{host}:{port}/")
    log(f"  data mode: {state.data_mode()}   releases: {', '.join(releases)}")
    log("  network access is disabled in this process; press Ctrl+C to stop")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    finally:
        httpd.server_close()
