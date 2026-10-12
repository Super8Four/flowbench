import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.annotations import annotate_run
from app.imports import prepare_run, prepare_source_documents
from app.projects import ProjectStore, validate_project_assignment
from app.runner import RunManager
from tests.test_annotations import sample_record


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.projects = ProjectStore(self.manager)
        self.project = self.projects.create({"name": "Circuit change", "description": "Disposable fixture"})
        self.before = sample_record()
        self.before["settings"]["project_id"] = self.project["id"]
        self.manager.save(self.before)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_baseline_validation_and_honest_comparisons(self):
        self.projects.update(self.project["id"], {"baseline_id": self.before["id"]})
        after = copy.deepcopy(self.before); after["id"] = "b" * 32
        after["summary"]["sum_received"]["bits_per_second"] = 200e6
        after["settings"]["streams"] = 2
        self.manager.save(after)
        failed = copy.deepcopy(after); failed.update(id="c" * 32, status="failed")
        self.manager.save(failed)
        report = self.projects.report(self.project["id"])
        self.assertEqual(report["comparisons"][0]["metrics"][0]["percent_change"], 100)
        self.assertEqual(report["comparisons"][0]["result"], "improved")
        self.assertFalse(report["comparisons"][0]["comparable"])
        self.assertIn("streams", report["comparisons"][0]["settings_differences"])
        self.assertEqual(report["excluded"], [{"id": failed["id"], "reason": "Run status is failed."}])
        for baseline_id in (failed["id"], "d" * 32):
            with self.assertRaises(ValueError):
                self.projects.update(self.project["id"], {"baseline_id": baseline_id})
        with self.assertRaises(ValueError):
            annotate_run(self.manager, self.before["id"], {"project_id": None})

    def test_missing_measurements_mixed_and_zero_baseline(self):
        self.before["summary"]["sum_sent"] = {"retransmits": 0}
        self.manager.save(self.before)
        self.projects.update(self.project["id"], {"baseline_id": self.before["id"]})
        after = copy.deepcopy(self.before); after["id"] = "b" * 32
        after["summary"]["sum_received"]["bits_per_second"] *= 2
        after["summary"]["sum_sent"]["retransmits"] = 4
        self.manager.save(after)
        no_metrics = copy.deepcopy(after); no_metrics.update(id="c" * 32, summary={})
        self.manager.save(no_metrics)
        report = self.projects.report(self.project["id"])
        self.assertEqual(report["comparisons"][0]["result"], "mixed")
        zero = next(row for row in report["comparisons"][0]["metrics"] if row["metric"].endswith("retransmits"))
        self.assertIsNone(zero["percent_change"])
        self.assertEqual(report["excluded"][0]["id"], no_metrics["id"])

    def test_restart_and_legacy_snapshot_preserve_catalog_and_notes(self):
        self.projects.update(self.project["id"], {"baseline_id": self.before["id"]})
        annotate_run(self.manager, self.before["id"], {"annotations": [{"text": "Changed link", "seconds": 2}]})
        snapshot = Path(self.temp.name) / "rollback"
        self.manager.storage.export_legacy(snapshot)
        restored = RunManager(snapshot, "missing-iperf")
        try:
            self.assertEqual(ProjectStore(restored).read(self.project["id"])["baseline_id"], self.before["id"])
            self.assertEqual(restored.read(self.before["id"])["annotations"], self.manager.read(self.before["id"])["annotations"])
        finally:
            restored.shutdown()
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.projects = ProjectStore(self.manager)
        self.assertEqual(self.projects.list()[0]["run_count"], 1)

    def test_export_import_remaps_atomic_dedup_and_membership(self):
        self.projects.update(self.project["id"], {"baseline_id": self.before["id"]})
        bundle = self.projects.export(self.project["id"])
        def prepare(source, filename):
            return [prepare_run(source, filename)]
        result = self.projects.import_bundle(bundle, "fixture.json", prepare)
        imported = self.manager.read(result["imported"][0])
        self.assertNotEqual(imported["id"], self.before["id"])
        self.assertEqual(imported["settings"]["project_id"], result["project"]["id"])
        self.assertEqual(result["project"]["baseline_id"], imported["id"])
        duplicate = self.projects.import_bundle(bundle, "again.json", prepare)
        self.assertEqual(duplicate["imported"], [])
        self.assertEqual(duplicate["duplicates"], [result["project"]["id"]])
        bad = copy.deepcopy(bundle); bad["runs"][0]["settings"]["project_id"] = "b" * 32
        with self.assertRaises(ValueError):
            self.projects.import_bundle(bad, "bad.json", prepare)
        with self.assertRaises(ValueError):
            validate_project_assignment(self.manager, {"project_id": "c" * 32})

    def test_import_failure_rolls_back_runs_and_catalog(self):
        bundle = self.projects.export(self.project["id"])
        before_catalog = self.projects.catalog()
        with patch.object(self.manager.storage, "save_metadata", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.projects.import_bundle(bundle, "fixture.json", lambda source, name: [prepare_run(source, name)])
        self.assertEqual(len(self.manager.storage.records()), 1)
        self.assertEqual(self.projects.catalog(), before_catalog)

    def test_complete_jobs_export_once_and_import_remaps_all_links(self):
        parent = sample_record("b" * 32, kind="protocol-job", output_format="protocol-job", summary={},
                               children=[{"id": "c" * 32, "protocol": "tcp"}, {"id": "d" * 32, "protocol": "udp"}])
        parent["settings"].update(protocol="both", project_id=self.project["id"])
        self.manager.save(parent)
        for index, (run_id, protocol) in enumerate((("c" * 32, "tcp"), ("d" * 32, "udp")), 1):
            child = sample_record(run_id, protocol_job={"id": parent["id"], "protocol": protocol, "order": index})
            child["settings"].update(project_id=self.project["id"], protocol=protocol)
            self.manager.save(child)
        self.projects.update(self.project["id"], {"baseline_id": "c" * 32})
        bundle = self.projects.export(self.project["id"])
        self.assertEqual(len(bundle["runs"]), 2)  # Standalone baseline fixture + complete Both job.
        result = self.projects.import_bundle(bundle, "job.json", prepare_source_documents)
        self.assertEqual(len(result["imported"]), 4)
        imported_parent = next(self.manager.read(run_id) for run_id in result["imported"]
                               if self.manager.read(run_id).get("kind") == "protocol-job")
        imported_children = [self.manager.read(row["id"]) for row in imported_parent["children"]]
        self.assertEqual({row["protocol_job"]["id"] for row in imported_children}, {imported_parent["id"]})
        self.assertEqual(result["project"]["baseline_id"], imported_children[0]["id"])
        self.assertEqual({row["settings"]["project_id"] for row in imported_children}, {result["project"]["id"]})
        self.assertEqual(result["project"]["created_at"], bundle["project"]["created_at"])

    def test_export_rejects_corrupt_cross_project_and_unreferenced_child_groups(self):
        foreign = sample_record("c" * 32)
        self.manager.save(foreign)
        parent = sample_record("b" * 32, kind="matrix-job", summary={}, children=[{"id": foreign["id"]}])
        parent["settings"]["project_id"] = self.project["id"]
        self.manager.save(parent)
        with self.assertRaisesRegex(ValueError, "another project"):
            self.projects.export(self.project["id"])
        parent["children"] = []
        self.manager.save(parent)
        self.before["matrix_job"] = {"id": parent["id"], "order": 1}
        self.manager.save(self.before)
        with self.assertRaisesRegex(ValueError, "inconsistent job membership"):
            self.projects.export(self.project["id"])


if __name__ == "__main__":
    unittest.main()
