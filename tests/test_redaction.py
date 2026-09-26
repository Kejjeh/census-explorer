"""Credential redaction: keys must not reach a log, manifest, export or error."""

from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from census_explorer import http_client, provenance
from census_explorer.redact import RedactedError, redact, redact_structure
from census_explorer.retrieve import acs_api
from tests.helpers import offline, temp_root

FAKE_KEY = "0123456789abcdef0123456789abcdef01234567"


class RedactionTests(unittest.TestCase):
    def test_key_query_parameter_is_redacted_even_without_the_environment(self):
        url = f"https://api.census.gov/data/2023/acs/acs5?get=NAME&key={FAKE_KEY}"
        out = redact(url)
        self.assertNotIn(FAKE_KEY, out)
        self.assertIn("key=[REDACTED]", out)

    def test_environment_value_is_redacted_wherever_it_appears(self):
        with mock.patch.dict(os.environ, {"CENSUS_API_KEY": FAKE_KEY}):
            out = redact(f"failed while using {FAKE_KEY} as a credential")
            self.assertNotIn(FAKE_KEY, out)
            self.assertIn("[REDACTED]", out)

    def test_sanitize_url_removes_the_parameter_entirely(self):
        url = f"https://api.census.gov/data?get=NAME&key={FAKE_KEY}&for=county:*"
        out = http_client.sanitize_url(url)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn("key=", out)
        self.assertIn("for=county", out)

    def test_nested_structures_are_redacted(self):
        with mock.patch.dict(os.environ, {"CENSUS_API_KEY": FAKE_KEY}):
            doc = redact_structure({"a": [f"x{FAKE_KEY}y", {"b": FAKE_KEY}], "n": 1})
        self.assertNotIn(FAKE_KEY, json.dumps(doc))
        self.assertEqual(doc["n"], 1)

    def test_redacted_error_message_is_clean(self):
        with mock.patch.dict(os.environ, {"CENSUS_API_KEY": FAKE_KEY}):
            err = RedactedError(f"boom {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, str(err))

    def test_manifest_written_to_disk_contains_no_key(self):
        with temp_root() as root, mock.patch.dict(os.environ, {"CENSUS_API_KEY": FAKE_KEY}):
            m = provenance.Manifest.new("t", "test", "live", root)
            m.add(provenance.RetrievalRecord(
                artifact_id="x", provider="p", kind="observations",
                source_url=f"https://example.invalid/?key={FAKE_KEY}",
                request={"note": f"used {FAKE_KEY}"},
                retrieved_at=provenance.utc_now(), http_status=200,
                content_bytes=1, sha256="0" * 64, cache_path="data/raw/x",
            ))
            path = root / "m.json"
            m.save(path)
            text = path.read_text(encoding="utf-8")
        self.assertNotIn(FAKE_KEY, text)
        self.assertIn("[REDACTED]", text)

    def test_missing_credential_message_names_the_variable_not_a_value(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(acs_api.has_api_key())
            with self.assertRaises(acs_api.MissingCredential) as ctx:
                acs_api.api_key()
        message = str(ctx.exception)
        self.assertIn("CENSUS_API_KEY", message)
        self.assertIn("summary-file", message)

    def test_api_key_is_read_from_the_environment_only_at_call_time(self):
        with mock.patch.dict(os.environ, {"CENSUS_API_KEY": FAKE_KEY}):
            self.assertEqual(acs_api.api_key(), FAKE_KEY)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(acs_api.has_api_key())


class OfflineGuardTests(unittest.TestCase):
    def test_fetch_refuses_while_offline(self):
        with offline():
            with self.assertRaises(http_client.NetworkDisabled):
                http_client.fetch("https://example.invalid/data")

    def test_stream_refuses_while_offline(self):
        with offline():
            with self.assertRaises(http_client.NetworkDisabled):
                list(http_client.stream_lines("https://example.invalid/data"))

    def test_importing_the_package_does_not_touch_the_network(self):
        # The guard proves intent; the real assurance is that no module-level
        # code in the package calls fetch. Re-importing under the guard would
        # raise if it did.
        import importlib

        with offline():
            for name in ("census_explorer", "census_explorer.dataset",
                         "census_explorer.server", "census_explorer.cli",
                         "census_explorer.retrieve.acs_api",
                         "census_explorer.retrieve.acs_summary_file"):
                importlib.reload(importlib.import_module(name))


if __name__ == "__main__":
    unittest.main()
