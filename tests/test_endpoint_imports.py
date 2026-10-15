"""Portable endpoint evidence is validated atomically without new observations."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from app.endpoints import derive_endpoints
from app.exports import xml_export
from app.imports import import_runs, parse_document
from app.runner import RunManager
try:
    from test_reports import report_fixture
except ModuleNotFoundError:
    from tests.test_reports import report_fixture


def endpoint_fixture():
    record = report_fixture()
    record["settings"].update(mode="bidir", streams=2)
    start = {"timestamp": {"timesecs": 1767261600}, "version": "iperf 3.22",
             "connecting_to": {"host": "endpoint-private.example", "port": 5201},
             "test_start": {"protocol": "TCP", "num_streams": 2, "duration": 10, "bidir": 1},
             "connected": [{"socket": 17, "local_host": "192.0.2.211", "local_port": 43117,
                            "remote_host": "198.51.100.213", "remote_port": 5201},
                           {"socket": 18, "local_host": "fe80::211%private_scope", "local_port": 43118,
                            "remote_host": "2001:db8::213", "remote_port": 5201}]}
    record["native_result"] = {"start": start, "end": copy.deepcopy(record["summary"]), "intervals": copy.deepcopy(record["intervals"])}
    record["endpoints"] = derive_endpoints(record)
    record["endpoints"]["execution_hostname"] = "execution-private.example"
    record["initiator_context"] = {"schema_version": 1, "observed_at": "2026-01-01T10:00:00Z",
                                   "source": "wsgi_remote_addr", "peer": {"address": "203.0.113.217", "port": None, "family": 4}}
    return record


class EndpointImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def load(self, value, xml=False):
        payload = xml_export(value) if xml else json.dumps(value).encode()
        return import_runs(self.manager, parse_document(payload), "fixture.xml" if xml else "fixture.json")

    def test_json_xml_reopen_retains_exact_context_raw_and_no_runtime_identity(self):
        for xml in (False, True):
            record = endpoint_fixture()
            record["id"] = ("a" if xml else "b") * 32
            with patch("app.endpoints.runtime_hostname", side_effect=AssertionError("No current-host lookup")), patch("socket.getaddrinfo", side_effect=AssertionError("No resolution")):
                identity = self.load(record, xml)["imported"][0]
            saved = self.manager.export(identity)
            self.assertEqual(saved["endpoints"], record["endpoints"])
            self.assertEqual(saved["initiator_context"], record["initiator_context"])
            self.assertEqual(saved["native_result"], record["native_result"])
            self.assertNotIn("endpoint_view", saved)
            self.manager.shutdown()
            self.manager = RunManager(self.temp.name, "missing-iperf")
            self.assertEqual(saved, self.manager.export(identity))

    def test_native_json_derives_preserved_sockets_without_current_host_or_context(self):
        native = endpoint_fixture()["native_result"]
        with patch("app.endpoints.runtime_hostname", side_effect=AssertionError("No host lookup")), patch("socket.getaddrinfo", side_effect=AssertionError("No resolution")):
            identity = self.load(native)["imported"][0]
        saved = self.manager.export(identity)
        self.assertEqual(saved["native_result"], native)
        self.assertEqual(len(saved["endpoints"]["sockets"]), 2)
        self.assertIsNone(saved["endpoints"]["execution_hostname"])
        self.assertNotIn("initiator_context", saved)

    def test_end_only_snapshot_keeps_previously_captured_streaming_pairs(self):
        for status in ("captured", "partial"):
            for xml in (False, True):
                with self.subTest(status=status, xml=xml):
                    record = endpoint_fixture()
                    record["id"] = ("c" if status == "partial" else "e") * 31 + ("d" if xml else "f")
                    record["endpoints"]["status"] = status
                    record["native_result"] = {"event": "end", "data": copy.deepcopy(record["summary"])}
                    identity = self.load(record, xml)["imported"][0]
                    self.assertEqual(self.manager.export(identity)["endpoints"], record["endpoints"])

    def test_invalid_second_batch_member_rolls_back_json_and_xml(self):
        from app.imports import prepare_run
        mutations = [lambda r: r["endpoints"].update(schema_version=2),
                     lambda r: r["endpoints"]["sockets"][0]["local"].update(port=True),
                     lambda r: r["endpoints"]["sockets"][0]["remote"].update(address="198.51.100.214"),
                     lambda r: r["initiator_context"]["peer"].update(port=1234),
                     lambda r: r["initiator_context"].update(extra_numeric=3405803985),
                     lambda r: r["initiator_context"].update(observed_at="2026-01-01T10:00:00+01:00")]
        for xml in (False, True):
            for mutate in mutations:
                good, bad = endpoint_fixture(), endpoint_fixture()
                prepare_run(good, "valid-first-member.json")
                bad["id"] = "c" * 32
                mutate(bad)
                # The actual typed comparison import validates all members
                # before writing any; no hand-built transaction simulation.
                value = {"schema_version": 1, "kind": "multi-comparison", "runs": [good, bad]}
                with self.assertRaises(ValueError):
                    self.load(value, xml)
                self.assertEqual(self.manager.storage.records(), [])

    def test_legacy_metadata_absence_is_preserved_and_view_field_not_imported(self):
        record = report_fixture()
        record["endpoint_view"] = {"execution_hostname": "never-trust-current-view.example"}
        identity = self.load(record)["imported"][0]
        saved = self.manager.export(identity)
        self.assertNotIn("endpoints", saved)
        self.assertNotIn("initiator_context", saved)
        self.assertNotIn("endpoint_view", saved)


if __name__ == "__main__":
    unittest.main()
