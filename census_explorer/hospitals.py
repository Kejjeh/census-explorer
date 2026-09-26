"""Offline hospital registry (Hospital Explorer phase 1).

Built only from a verified ``hospitals-*`` retrieval manifest. No network.

Data contracts
--------------
* **Two identifier systems, never merged.** An HFIS site is keyed by its
  ``fac_id`` (NYSDOH: "site specific facility identification number"). A CMS
  reporting entity is keyed by its CMS Certification Number (CCN). Both are
  kept as text exactly as published: leading zeros and letters survive.
* **One site per fac_id.** The HFIS General file repeats a site once per
  operator or cooperator. Site-level fields must agree across those rows;
  any disagreement is reported, and operators, cooperators and ownership
  types are kept as lists.
* **Certification is one-to-many.** Certification rows are kept per site in
  a separate document, row for row. They are never joined onto site rows,
  so no site is duplicated.
* **CCN to fac_id is evidence only.** No official crosswalk was found. The
  build lists *candidates* from one documented rule (exact normalized street
  address plus five-digit ZIP) and classifies each CCN as ``candidate``,
  ``ambiguous`` or ``unresolved``. Nothing is ever called a match, and CMS
  ratings are never copied onto HFIS sites.
* **Counties.** HFIS county codes are NYSDOH gazetteer codes, not FIPS.
  FIPS come from the Census Bureau's own county list (``NAME`` and ``GEOID``
  in the raw county boundary file of the default ACS release, checked
  against its retrieval manifest) by exact county name, plus reviewed
  spelling aliases in ``config/hospitals.json``. The NYS ITS Locality
  Hierarchy is an independent cross-check: every disagreement is reported,
  and a code it gives to two counties is never used.
* **Locations.** Coordinates are the published ones ("geo-coded to mailing
  address"). Missing or impossible coordinates leave a site unlocated, with
  a reason; nothing is geocoded or guessed.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import provenance
from .redact import RedactedError
from .retrieve import hospitals as retrieve

REGISTRY_DIR = Path("data/processed/hospitals")
#: 2: every site carries map_status / map_reason (a published point outside its
#: listed county is not drawn). Consumers refuse other versions.
REGISTRY_SCHEMA = 2
CCN_RE = re.compile(r"^[0-9A-Z]{6}$")
FAC_ID_RE = re.compile(r"^[0-9]+$")

#: Street-word abbreviations applied to both sides before the exact address
#: comparison. Listed here so the rule can be read and reviewed.
ADDRESS_ABBREVIATIONS = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "BOULEVARD": "BLVD", "PLACE": "PL", "LANE": "LN", "PARKWAY": "PKWY",
    "HIGHWAY": "HWY", "TURNPIKE": "TPKE", "COURT": "CT", "TERRACE": "TER",
    "EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S",
}
MATCH_RULE = ("exact equality of the normalized street address and the "
              "five-digit ZIP code. Normalization: upper case, punctuation to "
              "spaces, repeated spaces collapsed, and the street-word "
              "abbreviations listed in the report. Names are shown for review "
              "only and play no part in the rule.")

CMS_RATING_VALUES = {"1", "2", "3", "4", "5"}
DATA_MODES = {"live", "fixture"}


class RegistryError(RedactedError):
    """The registry cannot be built from these inputs."""


# -- small helpers -----------------------------------------------------------

def normalize_address(text: str) -> str:
    words = re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).split()
    return " ".join(ADDRESS_ABBREVIATIONS.get(w, w) for w in words)


def zip5(text: str) -> str | None:
    digits = (text or "").strip()
    if re.fullmatch(r"\d{5}(-?\d{4})?", digits):
        return digits[:5]
    return None


def iso_date(text: str) -> str | None:
    """MM/DD/YYYY (as both providers publish) to ISO, else None."""
    try:
        return _dt.datetime.strptime((text or "").strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def name_tokens(text: str) -> set[str]:
    return set(re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).split())


def name_overlap(a: str, b: str) -> float:
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    return round(len(ta & tb) / len(ta | tb), 2)


def parse_coordinate(lat_text: str, lon_text: str, bounds: dict) -> tuple[float | None, float | None, str]:
    lat_text, lon_text = (lat_text or "").strip(), (lon_text or "").strip()
    if not lat_text and not lon_text:
        return None, None, "missing"
    if not lat_text or not lon_text:
        return None, None, "incomplete"
    try:
        lat, lon = float(lat_text), float(lon_text)
    except ValueError:
        return None, None, "unparseable"
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None, None, "unparseable"
    if lat == 0 and lon == 0:
        return None, None, "zero"
    if not (bounds["min_lat"] <= lat <= bounds["max_lat"]
            and bounds["min_lon"] <= lon <= bounds["max_lon"]):
        return None, None, "outside_new_york"
    return lat, lon, "valid"


LOCATION_REASONS = {
    "valid": "Published coordinates, geocoded by NYSDOH to the mailing address.",
    "missing": "NYSDOH publishes no coordinates for this site.",
    "incomplete": "NYSDOH publishes only one of latitude and longitude.",
    "unparseable": "The published coordinates are not numbers.",
    "zero": "The published coordinates are 0, 0, a placeholder.",
    "outside_new_york": "The published coordinates fall outside New York State.",
}


MAP_STATUSES = ("mapped", "not_mapped_no_location", "not_mapped_county_conflict",
                "not_mapped_county_unverified")


def map_status(site: dict) -> tuple[str, str]:
    """Whether a site is drawn, and why, as a controlled status and a sentence.

    The contract: a site is drawn only where its published point (NYSDOH's
    geocode of the mailing address) lies inside the county HFIS lists for it.
    The listed county is what county filters use; the point is what the map
    uses; where they disagree, drawing the point under that county would put
    a mark somewhere the filter says it is not. Such sites stay in the
    directory and the CSV with both values, as published. Nothing is
    relabelled or geocoded.
    """
    listed = site.get("county_name") or site.get("county_name_hfis") or "the listed county"
    if site["location_status"] != "valid":
        return "not_mapped_no_location", site["location_reason"]
    check = site["location_county_check"]
    if check == "in_listed_county":
        return "mapped", ("Drawn at the published point, which NYSDOH geocoded from the "
                          f"mailing address and which lies in the listed county ({listed}).")
    if check == "in_other_county":
        point = site.get("location_in_county_name") or "another county"
        return "not_mapped_county_conflict", (
            f"Not drawn. HFIS lists this site in {listed} County, but its published point, "
            f"which NYSDOH geocoded from the mailing address, lies in {point} County "
            f"(FIPS {site.get('location_in_county_fips')}). Both are kept as published; "
            "neither is corrected.")
    return "not_mapped_county_unverified", (
        "Not drawn. The published point could not be placed in exactly one county "
        "shape, so it cannot be checked against the listed county.")


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _point_in_geometry(x: float, y: float, geom: dict) -> bool:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        if poly and _point_in_ring(x, y, poly[0]) and not any(
                _point_in_ring(x, y, hole) for hole in poly[1:]):
            return True
    return False


def county_containing(lat: float, lon: float, shapes: list[dict]) -> list[str]:
    return [f["properties"]["GEOID"] for f in shapes
            if f.get("geometry") and _point_in_geometry(lon, lat, f["geometry"])]


# -- inputs ------------------------------------------------------------------

def manifest_ids(repo_root: Path) -> list[str]:
    d = Path(repo_root) / "data" / "manifests"
    return sorted(p.stem for p in d.glob(retrieve.MANIFEST_PREFIX + "*.json"))


def load_inputs(repo_root: Path, manifest_id: str | None, cfg: dict) -> dict:
    """Verify a retrieval manifest and parse its artifacts. Fails on tampering."""
    repo_root = Path(repo_root)
    ids = manifest_ids(repo_root)
    if manifest_id is None:
        if not ids:
            raise RegistryError(
                "no hospital retrieval found. Run the explicit network step first: "
                "python -m census_explorer.cli hospitals fetch")
        manifest_id = ids[-1]
    path = repo_root / "data" / "manifests" / f"{manifest_id}.json"
    if not path.is_file():
        raise RegistryError(f"no manifest {manifest_id}")
    manifest = provenance.Manifest.load(path)
    problems = manifest.verify(repo_root)
    if problems:
        raise RegistryError(
            "the hospital retrieval does not match its manifest, so nothing was "
            "built: " + "; ".join(problems))
    if manifest.data_mode not in DATA_MODES:
        raise RegistryError(
            f"manifest {manifest_id} has data mode {manifest.data_mode!r}; only "
            f"{sorted(DATA_MODES)} are understood")
    pinned = (manifest.inputs or {}).get("retrieval_config_sha256")
    current = retrieve.canonical_sha256(cfg)
    if pinned is None:
        raise RegistryError(
            f"retrieval {manifest_id} does not record the retrieval configuration it "
            "was checked against, so it cannot be interpreted reproducibly. Run the "
            "explicit network step again: python -m census_explorer.cli hospitals fetch")
    if pinned != current:
        raise RegistryError(
            f"config/hospitals.json has changed since retrieval {manifest_id} "
            f"(pinned {pinned[:12]}, now {current[:12]}). Its sources, headers and "
            "hospital-family types were checked against the provider under the "
            "pinned version. Restore that version, or retrieve again under the new "
            "one: python -m census_explorer.cli hospitals fetch")
    by_id = {r["artifact_id"]: r for r in manifest.records}
    parsed: dict[str, Any] = {"manifest": manifest, "records": by_id}
    for key, src in cfg["sources"].items():
        rec = by_id.get(f"{key}:data")
        if rec is None:
            raise RegistryError(f"manifest {manifest_id} has no {key} data")
        body = (repo_root / rec["cache_path"]).read_bytes()
        header, rows, _ = retrieve.decode_csv(body)
        retrieve.check_header(key, header, src["expected_header"])
        expected_rows = manifest.inputs["sources"][key]["rows"]
        if len(rows) != expected_rows:
            raise RegistryError(
                f"{key}: {len(rows)} rows in the cache, {expected_rows} at retrieval")
        parsed[key] = [dict(zip(header, r)) for r in rows]
        meta_rec = by_id.get(f"{key}:metadata")
        parsed[key + ":metadata"] = (
            json.loads((repo_root / meta_rec["cache_path"]).read_bytes()) if meta_rec else {})
    return parsed


def column_descriptions(meta: dict) -> dict[str, str]:
    """Socrata column descriptions, keyed by the CSV (display) name."""
    return {c.get("name"): (c.get("description") or "").strip()
            for c in meta.get("columns", []) if c.get("name")}


# -- build -------------------------------------------------------------------

SITE_FIELDS = {
    "name": "Facility Name", "type": "Description", "type_short": "Short Description",
    "open_date_raw": "Facility Open Date", "address1": "Facility Address 1",
    "address2": "Facility Address 2", "city": "Facility City", "state": "Facility State",
    "zip": "Facility Zip Code", "phone": "Facility Phone Number",
    "hfis_county_code": "Facility County Code", "county_name_hfis": "Facility County",
    "regional_office": "Regional Office", "main_site_name": "Main Site Name",
    "main_site_fac_id": "Main Site Facility ID",
    "operating_certificate": "Operating Certificate Number",
    "lat_raw": "Facility Latitude", "lon_raw": "Facility Longitude",
}


def build_registry(inputs: dict, cfg: dict, *, rules: dict | None = None,
                   county_shapes: list[dict] | None = None,
                   shapes_source: dict | None = None) -> tuple[dict, dict]:
    """Return (registry, certification). Raises if any reconciliation fails.

    ``cfg`` is the retrieval configuration the manifest pinned; ``rules`` the
    transformation rules (``config/hospital_rules.json``), whose version and
    hash every output records. ``county_shapes``: GeoJSON-like features whose
    properties carry the Census ``NAME`` and ``GEOID`` of each NY county.
    """
    rules = rules if rules is not None else retrieve.load_rules()
    fam = cfg["hospital_family"]
    main_types = set(fam["main_site_types"])
    operated_types = set(fam["hospital_operated_types"])
    family = main_types | operated_types
    manifest = inputs["manifest"]
    msrc = manifest.inputs["sources"]
    checks: list[dict] = []

    def check(check_id: str, ok: bool, detail: str) -> None:
        checks.append({"check_id": check_id, "passed": bool(ok), "detail": detail})

    # ---- HFIS General: rows -> sites ----------------------------------
    general = inputs["nys_general"]
    type_counts = Counter(r["Description"] for r in general)
    fam_rows = [r for r in general if r["Description"] in family]
    excluded = {t: n for t, n in sorted(type_counts.items()) if t not in family}
    check("general_rows_partition",
          len(fam_rows) + sum(excluded.values()) == len(general),
          f"{len(fam_rows)} hospital-family rows + {sum(excluded.values())} excluded "
          f"rows = {len(general)} rows")
    check("general_family_rows_match_api", len(fam_rows) == msrc["nys_general"].get("family_rows"),
          f"{len(fam_rows)} hospital-family rows; provider count at retrieval "
          f"{msrc['nys_general'].get('family_api_count')}")
    fac_types_all = defaultdict(set)
    for r in general:
        fac_types_all[r["Facility ID"]].add(r["Description"])
    straddling = sorted(k for k, t in fac_types_all.items()
                        if t & family and t - family)
    check("no_fac_id_straddles_family", not straddling,
          f"{len(straddling)} facility IDs listed both inside and outside the hospital family")

    rows_by_id: dict[str, list[dict]] = defaultdict(list)
    for r in fam_rows:
        rows_by_id[r["Facility ID"]].append(r)

    if not county_shapes:
        raise RegistryError(
            "the Census county list is required for county FIPS codes; build the "
            "ACS release first or pass --shapes")
    census_fips: dict[str, str] = {}
    census_name_by_fips: dict[str, str] = {}
    for f in county_shapes:
        name = f["properties"]["NAME"]
        if name in census_fips:
            raise RegistryError(f"the Census county list names {name!r} twice")
        census_fips[name] = f["properties"]["GEOID"]
        census_name_by_fips[f["properties"]["GEOID"]] = name
    check("census_counties_unique", len(set(census_fips.values())) == len(census_fips),
          f"{len(census_fips)} Census counties, each with its own FIPS code")
    locality = [r for r in inputs["locality"] if r["Type Code"] == "1"]
    loc_fips_counts = Counter(r["County FIPS"] for r in locality)
    locality_fips = {r["County Name"]: r["County FIPS"] for r in locality}
    locality_duplicate_fips = {
        code: sorted(r["County Name"] for r in locality if r["County FIPS"] == code)
        for code, n in loc_fips_counts.items() if n > 1}
    aliases = {k: v for k, v in rules["county_name_aliases"].items() if k != "note"}
    bounds = rules["ny_bounds"]

    sites: list[dict] = []
    conflicts: list[dict] = []
    multi_row = 0
    code_names: dict[str, Counter] = defaultdict(Counter)
    for fac_id in sorted(rows_by_id, key=lambda x: (len(x), x)):
        rows = rows_by_id[fac_id]
        if len(rows) > 1:
            multi_row += 1
        site: dict[str, Any] = {"fac_id": fac_id, "source_rows": len(rows)}
        for field, col in SITE_FIELDS.items():
            values = sorted({r[col] for r in rows})
            if len(values) > 1:
                conflicts.append({"fac_id": fac_id, "field": col, "values": values})
            site[field] = rows[0][col]
        site["fac_id_is_numeric_text"] = bool(FAC_ID_RE.match(fac_id))
        site["type_group"] = "hospital" if site["type"] in main_types else "extension"
        # Sorted, so no output depends on the order of rows in the source file.
        site["operators"] = [dict(zip(("name", "city"), op)) for op in sorted(
            {(r["Operator Name"], r["Operator City"]) for r in rows})]
        site["cooperators"] = sorted({r["Cooperator Name"] for r in rows if r["Cooperator Name"]})
        owners = sorted({r["Ownership Type"] for r in rows if r["Ownership Type"]})
        site["ownership_types"] = owners
        site["ownership"] = (owners[0] if len(owners) == 1 else
                             "Listed with more than one ownership type" if owners else
                             "Not listed")
        site["open_date"] = iso_date(site["open_date_raw"])
        site["zip5"] = zip5(site["zip"])
        # county crosswalk
        code_names[site["hfis_county_code"]][site["county_name_hfis"]] += 1
        name = site["county_name_hfis"]
        alias = aliases.get(name, {})
        census_name = alias.get("census", name)
        fips = census_fips.get(census_name)
        site["county_fips"] = fips
        site["county_name"] = census_name if fips else name
        site["county_crosswalk"] = ("exact_name" if fips and census_name == name else
                                    "reviewed_alias" if fips else "unmatched")
        loc = locality_fips.get(alias.get("locality", name))
        site["locality_fips"] = loc
        site["locality_check"] = (
            "no_locality_row" if loc is None else
            "locality_code_shared" if loc in locality_duplicate_fips else
            "agrees" if loc == fips else "disagrees")
        # location
        lat, lon, status = parse_coordinate(site.pop("lat_raw"), site.pop("lon_raw"), bounds)
        site["lat"], site["lon"] = lat, lon
        site["location_status"] = status
        site["location_reason"] = LOCATION_REASONS[status]
        site["location_county_check"] = "not_checked"
        site["location_in_county_fips"] = None
        if status == "valid" and county_shapes:
            found = county_containing(lat, lon, county_shapes)
            site["location_in_county_fips"] = found[0] if len(found) == 1 else None
            site["location_county_check"] = (
                "outside_county_shapes" if not found else
                "several_counties" if len(found) > 1 else
                "in_listed_county" if found[0] == fips else
                "in_other_county")
        site["location_in_county_name"] = census_name_by_fips.get(site["location_in_county_fips"])
        site["map_status"], site["map_reason"] = map_status(site)
        sites.append(site)

    # Fail closed: a site whose repeated rows disagree on any site-level field
    # (identity, address, county, coordinates, main site, certificate) has no
    # authoritative value, and choosing one would make every derived field and
    # candidate depend on row order. Operators, cooperators and ownership
    # types legitimately differ between rows and are kept as lists instead.
    if conflicts:
        shown = "; ".join(f"fac_id {c['fac_id']} {c['field']}: {c['values']}"
                          for c in conflicts[:10])
        raise RegistryError(
            f"{len(conflicts)} site-level field conflict(s) across repeated HFIS "
            f"rows, so no registry was built: {shown}"
            + (" …" if len(conflicts) > 10 else "")
            + ". Resolve them with NYSDOH or exclude the sites by a reviewed rule; "
            "no value is chosen automatically.")
    by_id = {s["fac_id"]: s for s in sites}
    check("one_site_per_fac_id", len(by_id) == len(sites) == len(rows_by_id),
          f"{len(sites)} sites from {len(fam_rows)} rows ({multi_row} facility IDs "
          "repeat once per operator or cooperator)")

    # main-site relationships (as listed; never inferred)
    for s in sites:
        ms = s["main_site_fac_id"]
        if not ms:
            s["main_site_status"] = "is_main_site" if s["type_group"] == "hospital" else "not_listed"
        elif ms == s["fac_id"]:
            s["main_site_status"] = "self"
        elif ms in by_id:
            s["main_site_status"] = "listed"
            s["main_site_type"] = by_id[ms]["type"]
        else:
            s["main_site_status"] = "not_in_registry"
    for s in sites:
        s["operated_site_count"] = 0
    for s in sites:
        if s["main_site_status"] == "listed":
            by_id[s["main_site_fac_id"]]["operated_site_count"] += 1

    # ---- certification (one-to-many, kept apart) -----------------------
    cert_all = inputs["nys_certification"]
    conv = rules["certification"]["conversion_default_date"]
    certification: dict[str, list[dict]] = defaultdict(list)
    general_ids = set(fac_types_all)
    outside_family = orphan = 0
    orphan_ids: set[str] = set()
    type_mismatch = 0
    for r in cert_all:
        fid = r["Facility ID"]
        if fid not in by_id:
            if fid in general_ids:
                outside_family += 1
            else:
                orphan += 1
                orphan_ids.add(fid)
            continue
        if r["Description"] != by_id[fid]["type"]:
            type_mismatch += 1
        eff = iso_date(r["Effective Date"])
        kind = r["Attribute Type"]
        row = {
            "attribute_type": kind, "attribute_value": r["Attribute Value"],
            "measure_value_raw": r["Measure Value"], "sub_type": r["Sub Type"],
            "effective_date_raw": r["Effective Date"], "effective_date": eff,
            "effective_date_note": ("conversion_default" if eff == conv else
                                    "unparseable" if eff is None else ""),
        }
        if kind == "Bed":
            mv = (r["Measure Value"] or "").strip()
            whole = re.fullmatch(r"\d+", mv)
            row["certified_beds"] = int(mv) if whole else None
            row["count_note"] = ("" if whole else
                                 "no count published" if not mv else
                                 "published value is not a whole number")
        certification[fid].append(row)
    kept = sum(len(v) for v in certification.values())
    check("certification_rows_partition", kept + outside_family + orphan == len(cert_all),
          f"{kept} rows for registry sites + {outside_family} for other HFIS "
          f"facilities + {orphan} for IDs absent from HFIS General = {len(cert_all)}")
    check("certification_rows_match_api", len(cert_all) == msrc["nys_certification"]["rows"],
          f"{len(cert_all)} rows; provider count {msrc['nys_certification']['api_count']}")
    for fid, rows in certification.items():
        rows.sort(key=lambda x: (x["attribute_type"], x["attribute_value"], x["sub_type"],
                                 x["effective_date_raw"], x["measure_value_raw"]))
    dup_keys = [k for k, n in Counter(
        (fid, r["attribute_type"], r["attribute_value"], r["sub_type"])
        for fid, rows in certification.items() for r in rows).items() if n > 1]
    for s in sites:
        rows = certification.get(s["fac_id"], [])
        s["certification_rows"] = len(rows)
        # Every bed record, whole: category, count (or why there is none), sub
        # type and both forms of the date. Records are never added together.
        s["certified_beds"] = [
            {"category": r["attribute_value"], "beds": r["certified_beds"],
             "measure_value_raw": r["measure_value_raw"], "count_note": r["count_note"],
             "sub_type": r["sub_type"], "effective_date_raw": r["effective_date_raw"],
             "effective_date": r["effective_date"],
             "effective_date_note": r["effective_date_note"]}
            for r in rows if r["attribute_type"] == "Bed"]
        s["listed_services"] = sum(1 for r in rows if r["attribute_type"] == "Service")
    check("sites_unchanged_by_certification", len(sites) == len(by_id),
          "certification rows are held per site, never joined onto site rows")

    # ---- CMS entities ----------------------------------------------------
    footnotes = {r["Footnote"].strip(): r["Footnote Text"].strip()
                 for r in inputs["cms_footnotes"]}
    cms_all = inputs["cms_general"]
    cms_ny = [r for r in cms_all if r["State"] == "NY"]
    check("cms_ny_rows_match_api", len(cms_ny) == msrc["cms_general"].get("ny_rows"),
          f"{len(cms_ny)} New York rows of {len(cms_all)}; CMS datastore count at "
          f"retrieval {msrc['cms_general'].get('ny_api_count')}")
    ccn_counts = Counter(r["Facility ID"] for r in cms_ny)
    dup_ccn = sorted(k for k, n in ccn_counts.items() if n > 1)
    check("one_row_per_ccn", not dup_ccn, f"{len(dup_ccn)} duplicated CCNs")

    addr_index: dict[tuple, list[str]] = defaultdict(list)
    for s in sites:
        if s["zip5"]:
            addr_index[(normalize_address(s["address1"]), s["zip5"])].append(s["fac_id"])

    entities: list[dict] = []
    unknown_footnotes: Counter = Counter()
    for r in sorted(cms_ny, key=lambda x: x["Facility ID"]):
        ccn = r["Facility ID"]
        codes = [c.strip() for c in r["Hospital overall rating footnote"].split(",") if c.strip()]
        notes = []
        for c in codes:
            text = footnotes.get(c)
            if text is None:
                unknown_footnotes[c] += 1
            notes.append({"code": c, "text": text})
        raw = r["Hospital overall rating"].strip()
        rating = int(raw) if raw in CMS_RATING_VALUES else None
        zip_text = r["ZIP Code"].strip()
        if zip_text.isdigit() and len(zip_text) < 5:
            zip_text = zip_text.zfill(5)
        ent = {
            "ccn": ccn, "ccn_format_ok": bool(CCN_RE.match(ccn)),
            "name": r["Facility Name"], "address": r["Address"], "city": r["City/Town"],
            "zip": zip_text, "county_parish": r["County/Parish"],
            "phone": r["Telephone Number"], "hospital_type": r["Hospital Type"],
            "ownership": r["Hospital Ownership"],
            "emergency_services": r["Emergency Services"],
            "birthing_friendly": ("Meets the criteria" if r["Meets criteria for birthing friendly designation"] == "Y"
                                  else "Not indicated"),
            "overall_rating_raw": raw, "overall_rating": rating,
            "overall_rating_status": "rated" if rating is not None else "not_available",
            "overall_rating_footnotes": notes,
        }
        key = (normalize_address(r["Address"]), zip5(zip_text) or "")
        cands = []
        for fid in addr_index.get(key, []):
            s = by_id[fid]
            cands.append({"fac_id": fid, "name": s["name"], "type": s["type"],
                          "type_group": s["type_group"], "address1": s["address1"],
                          "zip": s["zip"], "normalized_address": key[0],
                          "name_token_overlap": name_overlap(r["Facility Name"], s["name"])})
        ent["hfis_candidates"] = cands
        entities.append(ent)

    site_ccns: dict[str, list[str]] = defaultdict(list)
    for e in entities:
        for c in e["hfis_candidates"]:
            if c["type_group"] == "hospital":
                site_ccns[c["fac_id"]].append(e["ccn"])
    for e in entities:
        mains = [c for c in e["hfis_candidates"] if c["type_group"] == "hospital"]
        shared = [c["fac_id"] for c in mains if len(site_ccns[c["fac_id"]]) > 1]
        if len(mains) == 1 and not shared:
            e["hfis_match_state"] = "candidate"
            e["hfis_match_note"] = ("One HFIS hospital has the same normalized address "
                                    "and ZIP. Unreviewed: not a confirmed match.")
        elif mains:
            e["hfis_match_state"] = "ambiguous"
            e["hfis_match_note"] = (
                "Several HFIS hospitals share this address and ZIP." if len(mains) > 1 else
                "The HFIS hospital at this address is also the candidate for "
                + ", ".join(x for x in site_ccns[shared[0]] if x != e["ccn"]) + ".")
        else:
            e["hfis_match_state"] = "unresolved"
            e["hfis_match_note"] = (
                "Only HFIS hospital extension sites share this address; no hospital does."
                if e["hfis_candidates"] else
                "No HFIS hospital-family site has this normalized address and ZIP.")
    ent_state = {e["ccn"]: e["hfis_match_state"] for e in entities}
    for s in sites:
        # A CCN's state describes its hospital candidates. An extension site at
        # the same address is evidence only, never itself a candidate.
        s["cms_candidates"] = [
            {"ccn": e["ccn"], "state": e["hfis_match_state"],
             "role": ("hospital_candidate" if c["type_group"] == "hospital"
                      else "same_address_extension_site")}
            for e in entities for c in e["hfis_candidates"] if c["fac_id"] == s["fac_id"]]

    # ---- reports -----------------------------------------------------------
    multi_name_codes = {c: dict(n) for c, n in code_names.items() if len(n) > 1}
    unmatched_counties = Counter(s["county_name_hfis"] for s in sites
                                 if s["county_crosswalk"] == "unmatched")
    loc_counts = Counter(s["location_status"] for s in sites)
    shape_counts = Counter(s["location_county_check"] for s in sites)
    check("county_codes_one_name", not multi_name_codes,
          f"{len(code_names)} HFIS county codes; codes with more than one name: "
          f"{len(multi_name_codes)}")
    failed = [c for c in checks if not c["passed"]]
    if failed:
        raise RegistryError("reconciliation failed: " + "; ".join(
            f"{c['check_id']} ({c['detail']})" for c in failed))

    gen_cols = column_descriptions(inputs.get("nys_general:metadata", {}))
    cert_cols = column_descriptions(inputs.get("nys_certification:metadata", {}))
    rec = inputs["records"]

    def src_block(key: str) -> dict:
        s = dict(msrc[key])
        data = rec[f"{key}:data"]
        s.update(retrieved_at=data["retrieved_at"], sha256=data["sha256"],
                 bytes=data["content_bytes"], source_url=data["source_url"],
                 terms=cfg["sources"][key]["terms"])
        return s

    registry = {
        "schema_version": REGISTRY_SCHEMA,
        "data_mode": manifest.data_mode,
        "built_at": provenance.utc_now(),
        "retrieval_manifest_id": manifest.manifest_id,
        "retrieval_code_revision": manifest.code_revision,
        "retrieval_config_sha256": manifest.inputs["retrieval_config_sha256"],
        "rules_version": rules.get("rules_version"),
        "rules_sha256": retrieve.canonical_sha256(rules),
        "sources": {k: src_block(k) for k in cfg["sources"]},
        "county_shapes": shapes_source,
        "dates": {
            "cms": ("CMS Hospital General Information, released "
                    f"{msrc['cms_general'].get('released')} (data modified "
                    f"{msrc['cms_general'].get('modified')}). Star-rating measure "
                    "periods vary by measure and are set by CMS for each release."),
            "nys": ("NYSDOH HFIS General rows updated "
                    + _epoch(msrc["nys_general"].get("rows_updated_at"))
                    + "; Certification rows updated "
                    + _epoch(msrc["nys_certification"].get("rows_updated_at")) + "."),
            "acs": ("Census tables on this page are 2019-2023 ACS five-year estimates, "
                    "a different product and period; nothing here combines them."),
        },
        "definitions": {
            "fac_id": gen_cols.get("Facility ID", ""),
            "hfis_county_code": gen_cols.get("Facility County Code", ""),
            "coordinates": gen_cols.get("Facility Latitude", ""),
            "measure_value": cert_cols.get("Measure Value", ""),
            "sub_type": cert_cols.get("Sub Type", ""),
            "effective_date": cert_cols.get("Effective Date", ""),
            "conversion_note": rules["certification"]["conversion_note"],
            "beds": ("Certified beds, one record per operating-certificate row, with "
                     "its sub type and effective date. Not staffed, available or "
                     "occupied beds, and not current capacity. Records are listed "
                     "separately and never added up."),
            "services": ("Services listed on the operating certificate. The "
                         "published measure value for services is not shown as a "
                         "count: it is 0 on most service rows, which does not fit "
                         "the dictionary's 'count of bed or service unit'."),
            "ccn": ("CMS Certification Number: identifies a Medicare reporting "
                    "entity, which can cover several campuses. It is not an HFIS "
                    "facility ID and is not assumed to equal one."),
            "overall_rating": ("CMS Overall Hospital Quality Star Rating of the "
                               "reporting entity (CCN), as published. Not a rating "
                               "of any one HFIS site."),
            "match_rule": MATCH_RULE,
            "address_abbreviations": ADDRESS_ABBREVIATIONS,
        },
        "links": {k: v for k, v in rules["links"].items()},
        "footnotes": footnotes,
        "sites": sites,
        "cms_entities": entities,
        "reports": {
            "checks": checks,
            "counts": {
                "cms_rows": len(cms_all), "cms_ny_rows": len(cms_ny),
                "cms_ny_by_type": dict(Counter(e["hospital_type"] for e in entities)),
                "nys_general_rows": len(general), "hospital_family_rows": len(fam_rows),
                "hospital_family_sites": len(sites),
                "hospital_sites": sum(1 for s in sites if s["type_group"] == "hospital"),
                "hospitals_listing_another_main_site": sum(
                    1 for s in sites if s["type_group"] == "hospital" and s["main_site_status"] == "listed"),
                "extension_sites": sum(1 for s in sites if s["type_group"] == "extension"),
                "sites_by_type": dict(Counter(s["type"] for s in sites)),
                "excluded_rows_by_type": excluded,
                "certification_rows": len(cert_all),
                "certification_rows_for_sites": kept,
                "certification_rows_other_facilities": outside_family,
                "certification_rows_unknown_ids": orphan,
                "certification_unknown_ids": sorted(orphan_ids),
            },
            "duplicates": {
                "fac_ids_with_repeated_rows": multi_row,
                "field_conflicts": conflicts,
                "sites_with_several_ownership_types": sorted(
                    s["fac_id"] for s in sites if len(s["ownership_types"]) > 1),
                "duplicated_ccns": dup_ccn,
                "repeated_certification_keys": len(dup_keys),
            },
            "counties": {
                "hfis_county_codes": len(code_names),
                "codes_with_several_names": multi_name_codes,
                "aliases_used": sorted({s["county_name_hfis"] for s in sites
                                        if s["county_crosswalk"] == "reviewed_alias"}),
                "unmatched_names": dict(unmatched_counties),
                "fips_source": "US Census Bureau county list (county boundary file of the default ACS release)",
                "locality_duplicate_fips": locality_duplicate_fips,
                "locality_disagreements": sorted({
                    (s["county_name_hfis"], s["county_fips"], s["locality_fips"], s["locality_check"])
                    for s in sites if s["locality_check"] != "agrees"}),
                "sites_by_county_fips": dict(sorted(Counter(
                    s["county_fips"] or "unmatched" for s in sites).items())),
            },
            "locations": {
                "by_status": dict(loc_counts),
                "county_shape_check": dict(shape_counts),
                "unlocated": [{"fac_id": s["fac_id"], "name": s["name"],
                               "status": s["location_status"]}
                              for s in sites if s["location_status"] != "valid"],
                "by_map_status": dict(Counter(s["map_status"] for s in sites)),
                "map_contract": (
                    "County filters use the county HFIS lists. The map draws a site only "
                    "where its published point (NYSDOH's geocode of the mailing address) "
                    "lies in that county; other sites stay listed with their reason."),
                "in_other_county": [{"fac_id": s["fac_id"], "name": s["name"],
                                     "listed_county": s["county_name"],
                                     "listed_fips": s["county_fips"],
                                     "point_county": s["location_in_county_name"],
                                     "point_fips": s["location_in_county_fips"],
                                     "map_status": s["map_status"]}
                                    for s in sites
                                    if s["location_county_check"] in ("in_other_county", "outside_county_shapes", "several_counties")],
            },
            "certification": {
                "by_attribute": dict(Counter(f"{r['attribute_type']} / {r['sub_type'] or '(blank)'}"
                                             for rows in certification.values() for r in rows)),
                "conversion_default_dates": sum(1 for rows in certification.values()
                                                for r in rows if r["effective_date_note"] == "conversion_default"),
                "service_rows_with_zero_measure": sum(1 for rows in certification.values()
                                                      for r in rows if r["attribute_type"] == "Service" and r["measure_value_raw"] == "0"),
                "rows_whose_type_differs_from_site": type_mismatch,
                "sites_without_rows": sorted(s["fac_id"] for s in sites if not s["certification_rows"]),
            },
            "cms_match": {
                "rule": MATCH_RULE,
                "by_state": dict(Counter(ent_state.values())),
                "by_state_and_type": {t: dict(Counter(e["hfis_match_state"] for e in entities
                                                      if e["hospital_type"] == t))
                                      for t in sorted({e["hospital_type"] for e in entities})},
                "sites_shared_by_ccns": {k: v for k, v in site_ccns.items() if len(v) > 1},
                "unknown_footnote_codes": dict(unknown_footnotes),
                "ccns_with_unusual_format": [e["ccn"] for e in entities if not e["ccn_format_ok"]],
                "rating_status": dict(Counter(e["overall_rating_status"] for e in entities)),
            },
        },
    }
    cert_doc = {
        "schema_version": REGISTRY_SCHEMA,
        "data_mode": manifest.data_mode,
        "retrieval_manifest_id": manifest.manifest_id,
        "rules_sha256": registry["rules_sha256"],
        "note": registry["definitions"]["beds"] + " " + registry["definitions"]["services"],
        "conversion_note": rules["certification"]["conversion_note"],
        "sites": {k: certification[k] for k in sorted(certification)},
    }
    return registry, cert_doc


def _epoch(value: Any) -> str:
    try:
        return _dt.datetime.fromtimestamp(int(value), _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return "unknown"


def census_county_source(repo_root: Path) -> tuple[Path, dict] | None:
    """The default release's raw county boundary file, as its manifest records it."""
    repo_root = Path(repo_root)
    try:
        project = json.loads((repo_root / "config" / "project.json").read_text("utf-8"))
        release = project["explorer"]["default_release"]
    except (OSError, KeyError, ValueError):
        return None
    best = None
    for path in sorted((repo_root / "data" / "manifests").glob(f"geography-{release}-*.json")):
        m = provenance.Manifest.load(path)
        for rec in m.records:
            if rec["artifact_id"].endswith(":county"):
                best = (repo_root / rec["cache_path"], {**rec, "manifest_id": m.manifest_id})
    return best


def load_census_counties(zip_path: Path, record: dict | None, state_fips: str = "36") -> tuple[list[dict], dict]:
    from . import shapefile
    body = Path(zip_path).read_bytes()
    digest = provenance.sha256_bytes(body)
    if record is not None and digest != record["sha256"]:
        raise RegistryError(
            f"{Path(zip_path).name} does not match its retrieval manifest "
            f"{record.get('manifest_id')}; the county list was not used")
    feats = [{"properties": {"NAME": f.properties["NAME"], "GEOID": f.properties["GEOID"]},
              "geometry": f.geometry}
             for f in shapefile.read_zip(zip_path) if f.properties.get("STATEFP") == state_fips]
    source = {
        "file": Path(zip_path).name, "sha256": digest,
        "retrieval_manifest_id": (record or {}).get("manifest_id"),
        "source_url": (record or {}).get("source_url"),
        "counties": len(feats),
        "note": ("Census county NAME and GEOID give each HFIS county its FIPS code, and "
                 "the same shapes check which county a published point falls in. "
                 "Cartographic boundaries are generalized (1:500,000, NAD83); a point "
                 "near a county line or shore can fall on the other side."),
    }
    return feats, source


def build(repo_root: Path, manifest_id: str | None = None,
          shapes_path: Path | None = None, out_dir: Path | None = None,
          log=print) -> dict:
    repo_root = Path(repo_root)
    cfg = retrieve.load_config()
    inputs = load_inputs(repo_root, manifest_id, cfg)
    if shapes_path is not None:
        zip_path, record = Path(shapes_path), None
    else:
        found = census_county_source(repo_root)
        if found is None:
            raise RegistryError(
                "no Census county boundary file recorded for the default release; "
                "run: python -m census_explorer.cli fetch geography")
        zip_path, record = found
    shapes, shapes_source = load_census_counties(zip_path, record)
    registry, cert = build_registry(inputs, cfg, county_shapes=shapes,
                                    shapes_source=shapes_source)
    registry["code_revision"] = provenance.code_revision(repo_root)
    write_outputs(Path(out_dir) if out_dir else repo_root / REGISTRY_DIR, registry, cert)
    log(f"hospital registry written to {Path(out_dir) if out_dir else REGISTRY_DIR}")
    return registry


OUTPUT_FILES = ("registry.json", "certification.json")


def write_outputs(out: Path, registry: dict, cert: dict) -> dict:
    """Write both documents and an index that pins them together.

    ``index.json`` names the retrieval, the data mode, the rules and the
    SHA-256 of both files. Every consumer checks it (``read_outputs``), so a
    registry and a certification file changed or mixed independently are
    refused rather than shown side by side.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    digests = {"registry.json": provenance.write_json(out / "registry.json", registry),
               "certification.json": provenance.write_json(out / "certification.json", cert)}
    index = {
        "schema_version": REGISTRY_SCHEMA,
        "retrieval_manifest_id": registry["retrieval_manifest_id"],
        "data_mode": registry["data_mode"],
        "rules_version": registry["rules_version"],
        "rules_sha256": registry["rules_sha256"],
        "retrieval_config_sha256": registry["retrieval_config_sha256"],
        "files": digests,
    }
    provenance.write_json(out / "index.json", index)
    return index


def check_pair(index: dict, registry: dict, cert: dict) -> None:
    """The parsed documents agree with each other and with their index."""
    for what, doc in (("index", index), ("registry", registry), ("certification", cert)):
        if doc.get("schema_version") != REGISTRY_SCHEMA:
            raise RegistryError(f"hospital {what} has schema version "
                                f"{doc.get('schema_version')!r}, expected {REGISTRY_SCHEMA}")
        if doc.get("data_mode") not in DATA_MODES:
            raise RegistryError(f"hospital {what} has unknown data mode {doc.get('data_mode')!r}")
    for key in ("retrieval_manifest_id", "data_mode", "rules_sha256"):
        values = {index.get(key), registry.get(key), cert.get(key)}
        if len(values) != 1:
            raise RegistryError(f"hospital registry files disagree on {key}: {sorted(map(str, values))}")


def read_outputs(out: Path) -> tuple[dict, bytes, bytes]:
    """Read a built registry, verifying every byte against its index."""
    out = Path(out)
    index_path = out / "index.json"
    if not index_path.is_file():
        raise RegistryError(
            f"{out} has no index.json, so its files cannot be checked; rebuild with: "
            "python -m census_explorer.cli hospitals build")
    index = json.loads(index_path.read_text("utf-8"))
    blobs = {}
    for name in OUTPUT_FILES:
        path = out / name
        if not path.is_file():
            raise RegistryError(f"{out} is missing {name}")
        body = path.read_bytes()
        if provenance.sha256_bytes(body) != (index.get("files") or {}).get(name):
            raise RegistryError(
                f"{out}/{name} does not match its index; it was changed after the "
                "build. Rebuild with: python -m census_explorer.cli hospitals build")
        blobs[name] = body
    check_pair(index, json.loads(blobs["registry.json"]), json.loads(blobs["certification.json"]))
    return index, blobs["registry.json"], blobs["certification.json"]


def summary_lines(registry: dict) -> list[str]:
    r = registry["reports"]
    c = r["counts"]
    lines = [
        f"retrieval manifest: {registry['retrieval_manifest_id']} ({registry['data_mode']}); "
        f"rules v{registry['rules_version']} {registry['rules_sha256'][:12]}",
        f"CMS Hospital General Information: {c['cms_rows']} rows, {c['cms_ny_rows']} in New York",
        f"NYS HFIS General: {c['nys_general_rows']} rows, {c['hospital_family_rows']} in the "
        f"hospital family -> {c['hospital_family_sites']} sites "
        f"({c['hospital_sites']} hospitals, of which {c['hospitals_listing_another_main_site']} "
        f"list another hospital as main site; {c['extension_sites']} extension sites)",
        f"  excluded rows by type: {c['excluded_rows_by_type']}",
        f"NYS HFIS Certification: {c['certification_rows']} rows, "
        f"{c['certification_rows_for_sites']} for registry sites",
        f"repeated fac_id rows: {r['duplicates']['fac_ids_with_repeated_rows']} IDs; "
        f"field conflicts: {len(r['duplicates']['field_conflicts'])}",
        f"counties: aliases {r['counties']['aliases_used']}, unmatched {r['counties']['unmatched_names']}",
        f"locations: {r['locations']['by_status']}; county shape check "
        f"{r['locations']['county_shape_check']}; map {r['locations']['by_map_status']}",
        f"CMS -> HFIS candidates: {r['cms_match']['by_state']}",
    ]
    for chk in r["checks"]:
        lines.append(f"  {'PASS' if chk['passed'] else 'FAIL'} {chk['check_id']}: {chk['detail']}")
    return lines
