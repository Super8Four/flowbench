import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from app.exports import xml_export
from app.imports import import_runs, parse_document, prepare_run
from app.runner import RunManager
from app.reruns import lineage_for
try:
    from .check_evidence import check_record
    from .test_imports import saved_run
except ImportError:
    from check_evidence import check_record
    from test_imports import saved_run


class CheckImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_json_xml_preserve_typed_result_restart_and_idempotency(self):
        for index, source in enumerate((check_record(), check_record("loss", run_id="b" * 32, outcome="upper_bound"))):
            original = copy.deepcopy(source)
            source["request_id"] = "e" * 32
            source["execution_request"] = {"request_id": "f" * 32}
            with patch("subprocess.Popen", side_effect=AssertionError("Imports never execute")):
                result = import_runs(self.manager, parse_document(json.dumps(source).encode()), "check.json")
                run_id = result["imported"][0]
                restored = self.manager.export(run_id)
                self.assertEqual(restored["result"], original["result"])
                self.assertEqual(restored["settings"], original["settings"])
                self.assertEqual(restored["metrics"], {})
                self.assertEqual(restored["series"], [])
                self.assertNotIn("request_id", restored)
                self.assertNotIn("execution_request", restored)
                self.assertIsNone(self.manager.storage.execution_request("e" * 32))
                self.assertEqual(import_runs(self.manager, parse_document(xml_export(source)), "check.xml")["duplicates"], [run_id])
            self.manager.shutdown()
            self.manager = RunManager(self.temp.name, "missing-iperf")
            self.assertEqual(self.manager.read(run_id)["result"], original["result"])

    def test_pending_and_running_snapshots_interrupt_both_lifecycle_levels(self):
        from app.checks import initial_result
        for status in ("pending", "running", "starting"):
            value = check_record(run_id=("a" if status == "pending" else "b") * 32)
            value.update(status=status, finished_at=None)
            value["result"] = initial_result("dns", value["settings"])
            value["result"]["status"] = "pending" if status == "starting" else status
            if status == "running":
                value["result"]["started_at"] = value["created_at"]
            restored, _ = prepare_run(value, "snapshot.json")
            self.assertEqual(restored["status"], "interrupted")
            self.assertEqual(restored["result"]["status"], "interrupted")
            self.assertIsNotNone(restored["result"]["finished_at"])
            self.assertEqual(restored["result"]["time_quality"]["status"], "unavailable")

    def test_malformed_or_fabricated_measurements_reject_entire_batch(self):
        good = check_record()
        variants = []
        for field, value in (("status", "failed"), ("summary", {"sum_received": {"bits_per_second": 1}}), ("intervals", [{"sum": {"end": 1}}]), ("test_parameters", {})):
            bad = check_record(run_id="b" * 32); bad[field] = value; variants.append(bad)
        bad = check_record(run_id="b" * 32); bad["result"]["summary"]["responses"] = 2; variants.append(bad)
        bad = check_record("loss", run_id="b" * 32, outcome="malformed"); bad["result"]["summary"]["nonresponse_percent"] = 100; variants.append(bad)
        for bad in variants:
            with self.subTest(bad=bad["check_type"]), self.assertRaises(ValueError):
                import_runs(self.manager, {"kind": "diagnostic-comparison", "runs": [good, bad]}, "bad.json")
            self.assertEqual(self.manager.storage.records(), [])

    def test_diagnostic_comparison_typed_xml_import(self):
        sources = [check_record(outcome="timeout"), check_record(run_id="b" * 32)]
        value = parse_document(xml_export({"schema_version": 1, "kind": "diagnostic-comparison", "runs": sources}))
        result = import_runs(self.manager, value, "comparison.xml")
        self.assertEqual(len(result["imported"]), 2)
        self.assertEqual([self.manager.read(i)["result"]["status"] for i in result["imported"]], ["timeout", "captured"])

    def test_lineage_remaps_reverse_order_and_existing_digest_parent(self):
        parent = saved_run(); child = check_record(run_id="b" * 32)
        child["lineage"] = lineage_for(parent["id"])
        original = copy.deepcopy(child)
        result = import_runs(self.manager, {"before": child, "after": parent}, "pair.json")
        child_id, parent_id = result["imported"]
        self.assertEqual(self.manager.read(child_id)["lineage"], lineage_for(parent_id))
        self.assertEqual(child, original)
        second = check_record(run_id="c" * 32); second["lineage"] = lineage_for(parent["id"])
        result = import_runs(self.manager, {"before": parent, "after": second}, "pair.json")
        self.assertEqual(result["duplicates"], [parent_id])
        self.assertEqual(self.manager.read(result["imported"][0])["lineage"]["source_run_id"], parent_id)

    def test_missing_ancestor_is_external_even_if_local_id_coincides(self):
        local = saved_run(); self.manager.save(local)
        child = check_record(run_id="b" * 32); child["lineage"] = lineage_for(local["id"])
        result = import_runs(self.manager, child, "child.json")
        lineage = self.manager.read(result["imported"][0])["lineage"]
        self.assertIsNone(lineage["source_run_id"])
        self.assertEqual(lineage["external_source_id"], local["id"])

    def test_complete_job_children_remap_lineage_inside_export(self):
        from app.runner import settings_from
        parent = saved_run(); parent.update(kind="protocol-job", summary={}, intervals=[], events=[])
        parent["settings"] = settings_from({"target": "office.example", "protocol": "both", "bandwidth": "2M"})
        children = []
        for index, protocol in enumerate(("tcp", "udp"), 1):
            child = saved_run(); child["id"] = ("b" if index == 1 else "c") * 32
            child["settings"]["protocol"] = protocol
            child["protocol_job"] = {"id": parent["id"], "protocol": protocol, "order": index}
            children.append(child)
        children[0]["lineage"] = dict(lineage_for(children[1]["id"]), source_run_id=None, external_source_id=children[1]["id"])
        parent["runs"] = children
        parent["children"] = [{"id": child["id"], "protocol": child["settings"]["protocol"]} for child in children]
        result = import_runs(self.manager, parent, "job.json")
        restored = self.manager.export(result["imported"][0])
        self.assertEqual(restored["runs"][0]["lineage"], lineage_for(restored["runs"][1]["id"]))
        self.assertEqual(restored["runs"][0]["protocol_job"]["id"], restored["id"])

    def test_cycles_ambiguous_sources_and_malformed_lineage_are_atomic(self):
        left = check_record(); right = check_record(run_id="b" * 32)
        cases = []
        self_link = copy.deepcopy(left); self_link["lineage"] = lineage_for(left["id"]); cases.append([self_link, right])
        a, b = copy.deepcopy(left), copy.deepcopy(right)
        a["lineage"] = lineage_for(b["id"])
        b["lineage"] = dict(lineage_for(a["id"]), source_run_id=None, external_source_id=a["id"])
        cases.append([a, b])
        conflicting = copy.deepcopy(left); conflicting["settings"]["label"] = "conflicting"; cases.append([left, conflicting])
        malformed = copy.deepcopy(right); malformed["lineage"] = dict(lineage_for(left["id"]), unexpected=True); cases.append([left, malformed])
        for rows in cases:
            with self.assertRaises(ValueError):
                import_runs(self.manager, {"kind": "diagnostic-comparison", "runs": rows}, "invalid.json")
            self.assertEqual(self.manager.storage.records(), [])

    def test_same_document_repeated_is_idempotent_and_transaction_failure_rolls_back(self):
        value = check_record()
        result = import_runs(self.manager, {"before": value, "after": copy.deepcopy(value)}, "same.json")
        self.assertEqual(len(result["imported"]), 1)
        self.assertEqual(result["duplicates"], result["imported"])
        original_save = self.manager.storage.save
        calls = []
        def fail_second(record, connection=None):
            calls.append(record["id"])
            if len(calls) == 2:
                raise OSError("synthetic disk failure")
            return original_save(record, connection)
        with patch.object(self.manager.storage, "save", side_effect=fail_second), self.assertRaises(OSError):
            import_runs(self.manager, {"before": check_record(run_id="b" * 32), "after": check_record(run_id="c" * 32)}, "pair.json")
        self.assertEqual(len(self.manager.storage.records()), 1)
