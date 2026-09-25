"""Hospital registry: retrieval checks and offline build behaviour.

Every source here is SYNTHETIC: a few made-up rows shaped like the real
exports, served by a fake fetcher. Nothing touches the network, and no
number here describes a real hospital.
"""

from __future__ import annotations

import csv
import io
import json
import unittest
import urllib.parse
from pathlib import Path

from census_explorer import hospitals as H
from census_explorer.http_client import Response
from census_explorer.retrieve import hospitals as R

from .helpers import offline, temp_root

CFG = R.load_config()


def _csv(header: list[str], rows: list[dict]) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([r.get(h, "") for h in header])
    return out.getvalue().encode("utf-8")


def cms_row(ccn, name, address, zip_, state="NY", rating="3", footnote="", **kw):
    row = {"Facility ID": ccn, "Facility Name": name, "Address": address,
           "City/Town": "TESTVILLE", "State": state, "ZIP Code": zip_,
           "County/Parish": "ALBANY", "Hospital Type": "Acute Care Hospitals",
           "Hospital Ownership": "Voluntary non-profit - Private",
           "Emergency Services": "Yes",
           "Meets criteria for birthing friendly designation": "Y",
           "Hospital overall rating": rating,
           "Hospital overall rating footnote": footnote}
    row.update(kw)
    return row


def gen_row(fac_id, desc, address="1 Test St", zip_="12345", county=("1", "Albany"),
            lat="42.65", lon="-73.75", main="", owner="Not for Profit Corporation",
            cooperator="", name=None):
    return {"Facility ID": fac_id, "Facility Name": name or f"Synthetic {fac_id}",
            "Description": desc, "Facility Address 1": address,
            "Facility City": "Testville", "Facility State": "New York",
            "Facility Zip Code": zip_, "Facility County Code": county[0],
            "Facility County": county[1], "Main Site Facility ID": main,
            "Ownership Type": owner, "Cooperator Name": cooperator,
            "Operator Name": "Synthetic Operator", "Facility Open Date": "01/01/1901",
            "Facility Latitude": lat, "Facility Longitude": lon}


def cert_row(fac_id, desc, kind, value, measure, sub, date):
    return {"Facility ID": fac_id, "Description": desc, "Attribute Type": kind,
            "Attribute Value": value, "Measure Value": measure, "Sub Type": sub,
            "Effective Date": date}


def default_rows():
    cms = [
        cms_row("330001", "SYNTHETIC GENERAL HOSPITAL", "100 MAIN STREET", "12345"),
        cms_row("33009F", "SYNTHETIC VA", "1 VA PLAZA", "12345", rating="Not Available",
                footnote="19", **{"Hospital Type": "Acute Care - Veterans Administration"}),
        cms_row("330002", "SHARED A", "5 Shared Road", "12345", rating="Not Available", footnote="99"),
        cms_row("330003", "SHARED B", "5 SHARED RD.", "12345"),
        cms_row("050001", "SYNTHETIC CALIFORNIA", "1 WEST ST", "90001", state="CA"),
    ]
    general = [
        gen_row("0101", "Hospital", "100 Main St", cooperator="Coop A"),
        gen_row("0101", "Hospital", "100 Main St", cooperator="Coop B"),
        gen_row("202", "Hospital Extension Clinic", main="0101", lat="", lon=""),
        gen_row("303", "Hospital", "5 Shared Road", lat="10", lon="-73.7",
                owner="County"),
        gen_row("303", "Hospital", "5 Shared Road", lat="10", lon="-73.7",
                owner="Municipality"),
        gen_row("404", "Adult Home"),
        gen_row("505", "Hospital", county=("44", "Saint Lawrence"), lat="44.6", lon="-75.0"),
        gen_row("606", "Off-Campus Emergency Department", main="999", lat="abc", lon="-73"),
    ]
    cert = [
        cert_row("0101", "Hospital", "Bed", "Intensive Care", "10", "Permanent", "03/21/1988"),
        cert_row("0101", "Hospital", "Bed", "Medical / Surgical", "120", "Permanent", "12/30/2008"),
        cert_row("0101", "Hospital", "Service", "Emergency Department", "0", "", "01/01/1991"),
        cert_row("202", "Hospital Extension Clinic", "Service", "Primary Care", "",
                 "Processing Stations", "01/01/2000"),
        cert_row("404", "Adult Home", "Service", "Adult Home", "0", "", "01/01/2000"),
        cert_row("9999", "Hospital", "Bed", "Pediatric", "5", "Permanent", "01/01/2000"),
    ]
    locality = [
        {"Type Code": "1", "Type": "County", "County Name": "Albany", "State FIPS": "36",
         "County Code": "001", "County FIPS": "36001"},
        # Reproduces the real flaw: St Lawrence listed with Seneca's code.
        {"Type Code": "1", "Type": "County", "County Name": "St Lawrence", "State FIPS": "36",
         "County Code": "099", "County FIPS": "36099"},
        {"Type Code": "1", "Type": "County", "County Name": "Seneca", "State FIPS": "36",
         "County Code": "099", "County FIPS": "36099"},
        {"Type Code": "3", "Type": "Town", "County Name": "Albany", "State FIPS": "36",
         "County Code": "001", "County FIPS": "36001"},
    ]
    footnotes = [{"Footnote": "19", "Footnote Text": "Synthetic footnote nineteen."},
                 {"Footnote": "22", "Footnote Text": "Synthetic footnote twenty-two."}]
    return {"cms_general": cms, "cms_footnotes": footnotes, "nys_general": general,
            "nys_certification": cert, "locality": locality}


def square(lat, lon, d=0.5):
    return {"type": "Polygon", "coordinates": [[[lon - d, lat - d], [lon + d, lat - d],
                                                [lon + d, lat + d], [lon - d, lat + d],
                                                [lon - d, lat - d]]]}


COUNTIES = [
    {"properties": {"NAME": "Albany", "GEOID": "36001"}, "geometry": square(42.65, -73.75)},
    {"properties": {"NAME": "St. Lawrence", "GEOID": "36089"}, "geometry": square(44.6, -75.0)},
    {"properties": {"NAME": "Seneca", "GEOID": "36099"}, "geometry": square(42.8, -76.8)},
]


class FakeFetcher:
    """Serves synthetic bodies for exactly the URLs the retrieval asks for."""

    def __init__(self, rows=None, header_override=None, count_offset=None,
                 ny_count_offset=0, family_count_offset=0, download_url=None):
        self.rows = rows or default_rows()
        self.header_override = header_override or {}
        self.count_offset = count_offset or {}
        self.ny_count_offset = ny_count_offset
        self.family_count_offset = family_count_offset
        self.download_url = download_url
        self.requested: list[str] = []

    def _body(self, key):
        header = self.header_override.get(key, CFG["sources"][key]["expected_header"])
        return _csv(header, self.rows[key])

    def __call__(self, url):
        self.requested.append(url)
        for key, src in CFG["sources"].items():
            n = len(self.rows[key]) + self.count_offset.get(key, 0)
            if src["kind"] == "cms_provider_data":
                data_url = self.download_url or f"https://data.cms.gov/files/{key}.csv"
                if url == src["metastore_url"]:
                    meta = {"identifier": src["dataset_id"], "title": src["title"],
                            "modified": "2026-07-22", "released": "2026-08-13",
                            "accessLevel": "public",
                            "distribution": [{"downloadURL": data_url,
                                              "describedBy": "https://data.cms.gov/dict.pdf"}]}
                    return Response(url, 200, json.dumps(meta).encode(), {})
                if url == data_url:
                    return Response(url, 200, self._body(key), {})
                if url == src["count_url"]:
                    return Response(url, 200, json.dumps({"count": n}).encode(), {})
                if url == src.get("ny_count_url"):
                    ny = sum(1 for r in self.rows[key] if r["State"] == "NY") + self.ny_count_offset
                    return Response(url, 200, json.dumps({"count": ny}).encode(), {})
            else:
                base = f"https://{src['domain']}"
                ident = src["dataset_id"]
                if url == f"{base}/api/views/{ident}.json":
                    meta = {"id": ident, "name": src["title"], "rowsUpdatedAt": 1788265277,
                            "attribution": src["publisher"],
                            "columns": [{"name": "Facility ID", "fieldName": "fac_id",
                                         "description": "Site specific facility identification number"}]}
                    return Response(url, 200, json.dumps(meta).encode(), {})
                if url == f"{base}/api/views/{ident}/rows.csv?accessType=DOWNLOAD":
                    return Response(url, 200, self._body(key), {})
                if url.startswith(f"{base}/resource/{ident}.json?"):
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
                    if "$where" in q:
                        fam = set(R.family_types(CFG))
                        n = sum(1 for r in self.rows[key] if r["Description"] in fam) \
                            + self.family_count_offset
                    return Response(url, 200, json.dumps([{"count": str(n)}]).encode(), {})
        if url == "https://data.cms.gov/dict.pdf" or url == CFG["documents"]["open_ny_terms"]["url"]:
            return Response(url, 200, b"%PDF-1.4 synthetic", {"content-type": "application/octet-stream"})
        raise AssertionError(f"unexpected URL {url}")


def fetch(root, **kw):
    return R.fetch_all(root, fetcher=FakeFetcher(**kw), stamp="20260101T000000+0000",
                       log=lambda *_: None)


class RetrievalChecks(unittest.TestCase):
    def test_complete_retrieval_writes_immutable_cache_and_manifest(self):
        with offline(), temp_root() as root:
            m = fetch(root)
            self.assertEqual(m.data_mode, "live")
            self.assertEqual(m.verify(root), [])
            self.assertEqual(m.inputs["sources"]["cms_general"]["ny_rows"], 4)
            self.assertEqual(m.inputs["sources"]["nys_general"]["family_rows"], 7)
            self.assertTrue((root / "data/manifests/hospitals-20260101T000000+0000.json").is_file())
            with self.assertRaisesRegex(R.SourceCheckFailed, "immutable"):
                fetch(root)

    def _assert_nothing_kept(self, root):
        self.assertFalse((root / "data/manifests").exists()
                         and any((root / "data/manifests").iterdir()))
        raw = root / "data/raw/hospitals"
        self.assertEqual(list(raw.iterdir()) if raw.exists() else [], [])

    def test_truncated_export_fails_and_keeps_nothing(self):
        with offline(), temp_root() as root:
            with self.assertRaisesRegex(R.SourceCheckFailed, "incomplete"):
                fetch(root, count_offset={"nys_certification": 1})
            self._assert_nothing_kept(root)

    def test_schema_drift_fails(self):
        header = list(CFG["sources"]["nys_general"]["expected_header"])
        header[header.index("Facility County Code")] = "County Code"
        with offline(), temp_root() as root:
            with self.assertRaisesRegex(R.SourceCheckFailed, "schema drift in nys_general"):
                fetch(root, header_override={"nys_general": header})
            self._assert_nothing_kept(root)

    def test_reordered_columns_are_drift_too(self):
        header = list(CFG["sources"]["cms_footnotes"]["expected_header"])[::-1]
        with offline(), temp_root() as root:
            with self.assertRaisesRegex(R.SourceCheckFailed, "different order"):
                fetch(root, header_override={"cms_footnotes": header})

    def test_state_filter_count_must_match_provider(self):
        with offline(), temp_root() as root:
            with self.assertRaisesRegex(R.SourceCheckFailed, "New York rows"):
                fetch(root, ny_count_offset=-1)
            with self.assertRaisesRegex(R.SourceCheckFailed, "hospital-family rows"):
                fetch(root, family_count_offset=2)

    def test_download_address_must_be_https_on_data_cms_gov(self):
        with offline(), temp_root() as root:
            with self.assertRaisesRegex(R.SourceCheckFailed, "not https on data.cms.gov"):
                fetch(root, download_url="https://example.org/Hospital_General_Information.csv")
            self._assert_nothing_kept(root)

    def test_malformed_csv_row_fails(self):
        with self.assertRaisesRegex(R.SourceCheckFailed, "different number of fields"):
            R.decode_csv(b"a,b\r\n1,2\r\n3\r\n")


class RegistryBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with offline(), temp_root() as root:
            fetch(root)
            inputs = H.load_inputs(root, None, CFG)
            cls.reg, cls.cert = H.build_registry(inputs, CFG, county_shapes=COUNTIES,
                                                 shapes_source={"file": "synthetic"})
        cls.sites = {s["fac_id"]: s for s in cls.reg["sites"]}
        cls.ents = {e["ccn"]: e for e in cls.reg["cms_entities"]}

    def test_identifiers_stay_text_with_leading_zeros_and_letters(self):
        self.assertIn("0101", self.sites)
        self.assertNotIn("101", self.sites)
        self.assertIn("33009F", self.ents)
        self.assertTrue(self.ents["33009F"]["ccn_format_ok"])
        self.assertTrue(all(isinstance(k, str) for k in self.sites))

    def test_repeated_rows_make_one_site_and_excluded_types_are_counted(self):
        self.assertEqual(sorted(self.sites), ["0101", "202", "303", "505", "606"])
        self.assertEqual(self.sites["0101"]["source_rows"], 2)
        self.assertEqual(self.sites["0101"]["cooperators"], ["Coop A", "Coop B"])
        c = self.reg["reports"]["counts"]
        self.assertEqual(c["excluded_rows_by_type"], {"Adult Home": 1})
        self.assertEqual(c["hospital_family_rows"] + 1, c["nys_general_rows"])

    def test_conflicting_ownership_is_a_distinct_value_not_a_guess(self):
        s = self.sites["303"]
        self.assertEqual(s["ownership_types"], ["County", "Municipality"])
        self.assertEqual(s["ownership"], "Listed with more than one ownership type")
        self.assertEqual(self.reg["reports"]["duplicates"]["sites_with_several_ownership_types"], ["303"])

    def test_main_site_relationship_is_kept_as_listed(self):
        self.assertEqual(self.sites["0101"]["main_site_status"], "is_main_site")
        self.assertEqual(self.sites["202"]["main_site_status"], "listed")
        self.assertEqual(self.sites["202"]["type_group"], "hospital_operated")
        self.assertEqual(self.sites["606"]["main_site_status"], "not_in_registry")
        self.assertEqual(self.sites["0101"]["operated_site_count"], 1)

    def test_certification_rows_are_preserved_without_fan_out(self):
        self.assertEqual(len(self.reg["sites"]), 5)
        self.assertEqual(len(self.cert["sites"]["0101"]), 3)
        c = self.reg["reports"]["counts"]
        self.assertEqual((c["certification_rows_for_sites"], c["certification_rows_other_facilities"],
                          c["certification_rows_unknown_ids"]), (4, 1, 1))
        self.assertEqual(c["certification_unknown_ids"], ["9999"])
        beds = self.sites["0101"]["certified_bed_categories"]
        self.assertEqual([(b["category"], b["beds"]) for b in beds],
                         [("Intensive Care", 10), ("Medical / Surgical", 120)])
        self.assertNotIn("total_beds", json.dumps(self.reg))
        rows = {r["attribute_value"]: r for r in self.cert["sites"]["0101"]}
        self.assertEqual(rows["Medical / Surgical"]["effective_date_note"], "conversion_default")
        self.assertEqual(rows["Intensive Care"]["effective_date"], "1988-03-21")
        self.assertNotIn("certified_beds", rows["Emergency Department"])
        self.assertEqual(rows["Emergency Department"]["measure_value_raw"], "0")

    def test_invalid_coordinates_leave_sites_unlocated_with_a_reason(self):
        self.assertEqual(self.sites["0101"]["location_status"], "valid")
        self.assertEqual(self.sites["202"]["location_status"], "missing")
        self.assertEqual(self.sites["303"]["location_status"], "outside_new_york")
        self.assertEqual(self.sites["606"]["location_status"], "unparseable")
        for fid in ("202", "303", "606"):
            self.assertIsNone(self.sites[fid]["lat"])
            self.assertTrue(self.sites[fid]["location_reason"])
        unlocated = {u["fac_id"] for u in self.reg["reports"]["locations"]["unlocated"]}
        self.assertEqual(unlocated, {"202", "303", "606"})

    def test_county_fips_come_from_census_and_locality_flaw_is_reported(self):
        s = self.sites["505"]
        self.assertEqual((s["county_fips"], s["county_crosswalk"]), ("36089", "reviewed_alias"))
        self.assertEqual(s["locality_check"], "locality_code_shared")
        self.assertEqual(s["location_county_check"], "in_listed_county")
        self.assertEqual(self.reg["reports"]["counties"]["locality_duplicate_fips"],
                         {"36099": ["Seneca", "St Lawrence"]})
        self.assertEqual(self.sites["0101"]["county_fips"], "36001")

    def test_ccn_candidates_are_evidence_never_matches(self):
        self.assertEqual(self.ents["330001"]["hfis_match_state"], "candidate")
        self.assertEqual(self.ents["330001"]["hfis_candidates"][0]["fac_id"], "0101")
        self.assertEqual(self.ents["330002"]["hfis_match_state"], "ambiguous")
        self.assertEqual(self.ents["330003"]["hfis_match_state"], "ambiguous")
        self.assertEqual(self.ents["33009F"]["hfis_match_state"], "unresolved")
        self.assertNotIn("050001", self.ents)
        states = {e["hfis_match_state"] for e in self.reg["cms_entities"]}
        self.assertNotIn("matched", states)
        # Entity quality is never copied onto a site.
        for s in self.reg["sites"]:
            self.assertFalse({"overall_rating", "overall_rating_raw"} & set(s))
        self.assertEqual(self.sites["303"]["cms_candidates"],
                         [{"ccn": "330002", "state": "ambiguous"},
                          {"ccn": "330003", "state": "ambiguous"}])

    def test_ratings_keep_footnotes_and_unavailable_is_not_zero(self):
        va = self.ents["33009F"]
        self.assertIsNone(va["overall_rating"])
        self.assertEqual(va["overall_rating_status"], "not_available")
        self.assertEqual(va["overall_rating_footnotes"],
                         [{"code": "19", "text": "Synthetic footnote nineteen."}])
        self.assertEqual(self.ents["330001"]["overall_rating"], 3)
        self.assertEqual(self.reg["reports"]["cms_match"]["unknown_footnote_codes"], {"99": 1})
        self.assertIsNone(self.ents["330002"]["overall_rating_footnotes"][0]["text"])

    def test_every_reconciliation_check_is_recorded(self):
        ids = {c["check_id"] for c in self.reg["reports"]["checks"]}
        self.assertTrue({"general_rows_partition", "certification_rows_partition",
                         "cms_ny_rows_match_api", "one_site_per_fac_id"} <= ids)
        self.assertTrue(all(c["passed"] for c in self.reg["reports"]["checks"]))

    def test_dates_stay_separate_by_provider(self):
        self.assertEqual(set(self.reg["dates"]), {"cms", "nys", "acs"})
        self.assertIn("2026-08-13", self.reg["dates"]["cms"])


class TamperedInputs(unittest.TestCase):
    def test_tampered_cache_stops_the_build(self):
        with offline(), temp_root() as root:
            fetch(root)
            path = next((root / "data/raw/hospitals").glob("*/nys_general.csv"))
            path.write_bytes(path.read_bytes().replace(b"Synthetic 0101", b"Synthetic 0102"))
            with self.assertRaisesRegex(H.RegistryError, "does not match its manifest"):
                H.load_inputs(root, None, CFG)

    def test_no_retrieval_means_no_build(self):
        with offline(), temp_root() as root:
            with self.assertRaisesRegex(H.RegistryError, "hospitals fetch"):
                H.load_inputs(root, None, CFG)

    def test_county_list_is_required_and_mismatched_file_is_refused(self):
        with offline(), temp_root() as root:
            fetch(root)
            inputs = H.load_inputs(root, None, CFG)
            with self.assertRaisesRegex(H.RegistryError, "Census county list"):
                H.build_registry(inputs, CFG, county_shapes=[])
            zp = root / "c.zip"
            zp.write_bytes(b"not the recorded file")
            with self.assertRaisesRegex(H.RegistryError, "does not match its retrieval manifest"):
                H.load_census_counties(zp, {"sha256": "0" * 64, "manifest_id": "geography-x"})

    def test_duplicated_ccn_fails_reconciliation(self):
        rows = default_rows()
        rows["cms_general"].append(cms_row("330001", "DUP", "9 ELSEWHERE", "12345"))
        with offline(), temp_root() as root:
            fetch(root, rows=rows)
            inputs = H.load_inputs(root, None, CFG)
            with self.assertRaisesRegex(H.RegistryError, "one_row_per_ccn"):
                H.build_registry(inputs, CFG, county_shapes=COUNTIES)


class Normalization(unittest.TestCase):
    def test_address_rule_is_exact_after_documented_abbreviations(self):
        self.assertEqual(H.normalize_address("5 Shared Road"), H.normalize_address("5 SHARED RD."))
        self.assertNotEqual(H.normalize_address("5 Shared Road"), H.normalize_address("7 Shared Road"))
        self.assertEqual(H.zip5("12345-6789"), "12345")
        self.assertIsNone(H.zip5("1234"))

    def test_coordinates(self):
        b = CFG["ny_bounds"]
        self.assertEqual(H.parse_coordinate("0", "0", b)[2], "zero")
        self.assertEqual(H.parse_coordinate("nan", "-73", b)[2], "unparseable")
        self.assertEqual(H.parse_coordinate("42.1", "", b)[2], "incomplete")
        self.assertEqual(H.parse_coordinate("42.1", "-73.5", b)[2], "valid")
        self.assertEqual(H.parse_coordinate("40.7", "-74.5", b)[2], "valid")
        self.assertEqual(H.parse_coordinate("39.9", "-75.1", b)[2], "outside_new_york")


if __name__ == "__main__":
    unittest.main()
