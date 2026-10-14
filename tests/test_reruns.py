"""Disposable archives only: atomic admission, no replay and immutable sources."""
import copy
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from app.runner import RunManager
from app.reruns import AdmissionConflict, AdmissionUnavailable, validate_lineage_graph
from test_annotations import sample_record


class RerunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.manager.capabilities.update(available=True, bidir=True, udp_bidir=True, json_stream=True)
        self.source = sample_record()
        self.source["settings"].update(notes="Configuration note", color="#123456", tags={"site": "Lab"})
        self.source["annotations"] = [{"text": "Old observed event"}]
        self.manager.save(self.source)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def reopen(self):
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")

    def test_lost_response_replay_precedes_source_busy_capability_and_restart(self):
        plan = self.manager.rerun_plan(self.source["id"])
        body = {"request_id": "b" * 32, "source_fingerprint": plan["source_fingerprint"]}
        with patch.object(self.manager, "run") as worker:
            first = self.manager.rerun(self.source["id"], body)
            self.manager.worker.join(2)
        self.assertFalse(first["replayed"])
        new = first["run"]
        self.assertNotEqual(new["id"], self.source["id"])
        self.assertNotEqual(new["created_at"], self.source["created_at"])
        self.assertEqual(new["settings"]["notes"], "Configuration note")
        self.assertEqual(new["settings"]["color"], "#123456")
        self.assertNotIn("annotations", new)
        self.assertEqual(new["summary"], {})
        self.assertEqual(new["lineage"]["source_run_id"], self.source["id"])
        self.assertEqual(self.manager.storage.read(self.source["id"]), self.source)
        self.manager.capabilities["available"] = False
        with patch.object(self.manager, "rerun_plan", side_effect=AssertionError("source gate reached")):
            retry = self.manager.rerun(self.source["id"], body)
        self.assertTrue(retry["replayed"])
        self.assertEqual(retry["run"]["id"], new["id"])
        worker.assert_called_once()
        self.reopen()
        retry = self.manager.rerun(self.source["id"], body)
        self.assertEqual(retry["run"]["status"], "interrupted")
        self.assertEqual(retry["run"]["id"], new["id"])
        with self.assertRaises(AdmissionConflict):
            self.manager.rerun(self.source["id"], dict(body, confirm_netserver=True))

    def test_transaction_failure_rolls_back_children_and_request_claim(self):
        body = {"target": "127.0.0.1", "protocol": "both", "request_id": "c" * 32}
        create = self.manager.storage.create
        writes = []
        def fail_child(record, connection):
            writes.append(record["id"])
            if len(writes) == 3:
                raise OSError("Fixture disk error")
            create(record, connection)
        with patch.object(self.manager.storage, "create", side_effect=fail_child), patch("app.runner.threading.Thread") as thread:
            with self.assertRaises(OSError):
                self.manager.start(body)
            thread.assert_not_called()
        self.assertEqual(len(self.manager.storage.records()), 1)
        self.assertIsNone(self.manager.storage.execution_request(body["request_id"]))
        self.assertIsNone(self.manager.active)
        with patch.object(self.manager, "run_job"):
            root = self.manager.start(body)
            self.manager.worker.join(2)
        self.assertEqual(len(root["children"]), 2)

    def test_launch_failure_is_durable_and_retry_never_launches_again(self):
        body = {"target": "127.0.0.1", "request_id": "d" * 32}
        with patch("app.runner.threading.Thread.start", side_effect=RuntimeError("Fixture launch failure")):
            with self.assertRaises(AdmissionUnavailable) as failure:
                self.manager.start(body)
        failed = failure.exception.record
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(self.manager.worker)
        self.assertIsNone(self.manager.active)
        self.reopen()
        with patch("app.runner.threading.Thread.start") as launch:
            replay = self.manager.start(body)
        launch.assert_not_called()
        self.assertEqual(replay["id"], failed["id"])
        self.assertEqual(replay["status"], "failed")

    def test_concurrent_same_token_has_one_atomic_admission(self):
        body = {"target": "127.0.0.1", "request_id": "e" * 32}
        barrier, results, errors = threading.Barrier(3), [], []
        def submit():
            barrier.wait()
            try:
                results.append(self.manager.start(body))
            except Exception as exc:
                errors.append(exc)
        with patch.object(self.manager, "run") as worker:
            threads = [threading.Thread(target=submit) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(3)
            self.manager.worker.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], results[1]["id"])
        worker.assert_called_once()

    def test_plan_stale_and_missing_fresh_consent_do_not_write(self):
        plan = self.manager.rerun_plan(self.source["id"])
        changed = copy.deepcopy(self.source)
        changed["settings"]["duration"] = 2
        self.manager.save(changed)
        with self.assertRaises(AdmissionConflict):
            self.manager.rerun(self.source["id"], {"request_id": "f" * 32, "source_fingerprint": plan["source_fingerprint"]})
        changed["settings"].update(role="server", target="private-saved-peer.test", idle_timeout=300)
        changed["incoming"] = {"listener": True}
        self.manager.save(changed)
        plan = self.manager.rerun_plan(changed["id"])
        self.assertEqual(plan["required_confirmations"], ["confirm_wait_for_client"])
        self.assertEqual(plan["config"]["target"], "")
        self.assertEqual(plan["config"]["idle_timeout"], 60)
        with self.assertRaises(ValueError):
            self.manager.rerun(changed["id"], {"request_id": "f" * 32})
        self.assertEqual(len(self.manager.storage.records()), 1)
        with patch.object(self.manager, "run"):
            incoming = self.manager.rerun(changed["id"], {"request_id": "f" * 32, "confirm_wait_for_client": True})["run"]
            self.manager.worker.join(2)
        self.assertIn("-s", incoming["command"])
        self.assertIn("-1", incoming["command"])
        self.assertNotIn("-c", incoming["command"])
        self.assertNotIn("incoming", incoming)
        self.assertEqual(incoming["settings"]["target"], "")

    def test_check_token_works_without_iperf_and_recovery_does_not_probe(self):
        from app.checks import validate_check_record
        self.manager.capabilities["available"] = False
        body = {"check_type": "dns", "target": "example.test", "request_id": "1" * 32}
        with patch.object(self.manager, "run_check") as worker:
            pending = self.manager.start_check(body)
            self.manager.worker.join(2)
            duplicate = self.manager.start_check(body)
        self.assertEqual(pending["id"], duplicate["id"])
        self.assertEqual(pending["settings"]["target"], "example.test.")
        self.assertNotIn("protocol", pending["settings"])
        worker.assert_called_once()
        self.reopen()
        recovered = self.manager.read(pending["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(recovered["result"]["status"], "interrupted")
        self.assertEqual(recovered["result"]["time_quality"]["status"], "unavailable")
        validate_check_record(recovered)
        self.assertEqual(self.manager.start_check(body)["id"], pending["id"])
        row = next(row for row in self.manager.list_runs() if row["id"] == pending["id"])
        self.assertEqual(row["check_type"], "dns")
        self.assertEqual(row["result"]["status"], "interrupted")
        self.assertNotIn("attempts", row["result"])
        self.assertEqual(row["result"]["summary"], recovered["result"]["summary"])
        self.assertIn("attempts", self.manager.export(pending["id"])["result"])

    def test_lineage_cycle_rejected(self):
        from app.reruns import lineage_for
        with self.assertRaises(ValueError):
            validate_lineage_graph([{"id": "a" * 32, "lineage": lineage_for("b" * 32)},
                                    {"id": "b" * 32, "lineage": lineage_for("a" * 32)}])

    def test_check_cancel_checkpoints_and_retains_typed_result(self):
        from app.checks import initial_result, validate_check_record
        from app.runner import now
        from app.time_quality import time_quality_result
        ready, cancelled = threading.Event(), threading.Event()
        class Capture:
            def __init__(self, kind, settings, cancelled):
                self.result = initial_result(kind, settings)
            def run(self, checkpoint):
                self.result.update(status="running", started_at=now(), time_quality=time_quality_result("unavailable"))
                checkpoint(copy.deepcopy(self.result))
                ready.set()
                if not cancelled.wait(3):
                    raise AssertionError("Cancellation never reached owned capture")
                self.result.update(status="cancelled", finished_at=now())
                checkpoint(copy.deepcopy(self.result))
                return self.result
            def cancel(self):
                cancelled.set()
        with patch("app.checks.CheckCapture", Capture):
            record = self.manager.start_check({"check_type": "loss", "target": "127.0.0.1", "count": 1})
            self.assertTrue(ready.wait(3))
            self.manager.stop(record["id"])
            self.manager.worker.join(3)
        retained = self.manager.read(record["id"])
        self.assertEqual(retained["status"], "cancelled")
        self.assertIsNotNone(retained["started_at"])
        self.assertEqual(retained["created_at"], record["created_at"])
        self.assertIsNone(self.manager.active)
        validate_check_record(retained)

    def test_check_checkpoint_storage_error_stops_capture_and_is_not_probe_success(self):
        from app.checks import initial_result, validate_check_record
        from app.runner import now
        stopped = threading.Event()
        class Capture:
            def __init__(self, kind, settings, cancelled):
                self.result = initial_result(kind, settings)
            def run(self, checkpoint):
                self.result.update(status="running", started_at=now())
                checkpoint(self.result)
                raise AssertionError("Probe continued past archive checkpoint failure")
            def cancel(self):
                stopped.set()
        save = self.manager.save
        def fail_checkpoint(record):
            if record.get("kind") == "diagnostic-check" and record["result"]["status"] == "running":
                raise OSError("Fixture checkpoint disk failure")
            save(record)
        with patch("app.checks.CheckCapture", Capture), patch.object(self.manager, "save", side_effect=fail_checkpoint):
            record = self.manager.start_check({"check_type": "dns", "target": "example.test"})
            self.manager.worker.join(3)
        retained = self.manager.read(record["id"])
        self.assertTrue(stopped.is_set())
        self.assertEqual(retained["status"], "failed")
        self.assertEqual(retained["result"]["status"], "error")
        self.assertIn("checkpoint disk failure", self.manager.storage_error)
        self.assertEqual(retained["result"]["attempts"], [])
        validate_check_record(retained)

    def test_unreaped_check_child_retains_writer_until_exit(self):
        child = Mock()
        child.poll.return_value = None
        class Capture:
            def __init__(self, *args, **kwargs):
                self.processes = {child}
            def run(self, checkpoint):
                raise RuntimeError("Fixture child could not be reaped")
            def cancel(self):
                child.kill()
        with patch("app.checks.CheckCapture", Capture):
            record = self.manager.start_check({"check_type": "loss", "target": "127.0.0.1"})
            self.manager.worker.join(3)
        self.assertEqual(self.manager.active_id, record["id"])
        self.assertIn("cleanup", self.manager.storage_error)
        self.manager.shutdown()
        self.assertFalse(self.manager.storage_lock.closed)
        child.poll.return_value = 0
        self.manager.shutdown()
        self.assertTrue(self.manager.storage_lock.closed)

    def test_protocol_and_matrix_rerun_remap_parent_child_lineage(self):
        for kind, config in (("protocol-job", {"target": "127.0.0.1", "protocol": "both"}),
                             ("matrix-job", {"settings": {"target": "127.0.0.1", "duration": 1, "bandwidth": "1M"},
                                             "protocols": ["tcp"], "modes": ["upload", "download"], "streams": [1]})):
            self.manager.capabilities["available"] = True
            with patch.object(self.manager, "run_job"):
                first = self.manager.start_matrix(config) if kind == "matrix-job" else self.manager.start(config)
                self.manager.worker.join(2)
            for reference in first["children"]:
                child = self.manager.storage.read(reference["id"])
                child.update(status="failed", finished_at="2026-01-01T00:00:01+00:00")
                self.manager.save(child)
            first.update(status="failed", finished_at="2026-01-01T00:00:01+00:00")
            self.manager.save(first)
            self.manager.job = self.manager.record = first
            self.manager.active = None
            with patch.object(self.manager, "run_job"):
                rerun = self.manager.rerun(first["id"], {"request_id": ("2" if kind == "protocol-job" else "3") * 32})["run"]
                self.manager.worker.join(2)
            self.assertEqual(rerun["kind"], kind)
            for old, new in zip(first["children"], rerun["children"]):
                row = self.manager.storage.read(new["id"])
                self.assertNotEqual(old["id"], new["id"])
                self.assertEqual(row["lineage"]["source_run_id"], old["id"])
                self.assertEqual(row["matrix_job" if kind == "matrix-job" else "protocol_job"]["id"], rerun["id"])
                self.assertEqual(row["created_at"], rerun["created_at"])
            self.manager.active = None

    def test_flent_rerun_requires_fresh_consent_and_token_replay_precedes_tools(self):
        source = copy.deepcopy(self.source)
        source.update(kind="flent-run", settings=dict(source["settings"], flent_workload="rrul", port=12865))
        self.manager.save(source)
        self.manager.flent_capabilities.update(available=True, version="Fixture Flent", netperf_version="Fixture Netperf")
        plan = self.manager.rerun_plan(source["id"])
        self.assertEqual(plan["required_confirmations"], ["confirm_netserver"])
        body = {"request_id": "4" * 32}
        with self.assertRaises(ValueError):
            self.manager.rerun(source["id"], body)
        body["confirm_netserver"] = True
        with patch.object(self.manager, "run_flent"):
            first = self.manager.rerun(source["id"], body)
            self.manager.worker.join(2)
        self.manager.flent_capabilities["available"] = False
        self.assertEqual(self.manager.rerun(source["id"], body)["run"]["id"], first["run"]["id"])

    def test_actual_loopback_check_project_baseline_report_export_and_import(self):
        from app.projects import ProjectStore
        from app.imports import prepare_source_documents
        from app.time_quality import time_quality_result
        from test_checks import LocalDNS
        projects = ProjectStore(self.manager)
        project = projects.create({"name": "Disposable DNS observation"})
        with LocalDNS(mode="nxdomain") as dns, patch("app.checks.capture_time_quality", return_value=time_quality_result("unavailable")):
            first = self.manager.start_check({"check_type": "dns", "target": "check.invalid", "resolver_mode": "custom",
                                              "resolver_address": "127.0.0.1", "resolver_port": dns.port,
                                              "project_id": project["id"], "request_id": "6" * 32})
            self.manager.worker.join(5)
            self.assertEqual(self.manager.read(first["id"])["status"], "completed")
            second = self.manager.rerun(first["id"], {"request_id": "7" * 32})["run"]
            self.manager.worker.join(5)
            self.assertEqual(self.manager.read(second["id"])["status"], "completed")
        projects.update(project["id"], {"baseline_id": first["id"]})
        report = projects.report(project["id"])
        self.assertEqual(report["comparisons"][0]["kind"], "diagnostic-check-comparison")
        self.assertNotIn("metrics", report["comparisons"][0])
        self.assertEqual(report["comparisons"][0]["result"], "observations compared")
        bundle = projects.export(project["id"])
        imported = projects.import_bundle(bundle, "disposable.json", prepare_source_documents)
        copied = projects.export(imported["project"]["id"])
        by_original = {row["imported"]["source_id"]: row for row in copied["runs"]}
        self.assertEqual(by_original[second["id"]]["lineage"]["source_run_id"], by_original[first["id"]]["id"])
        self.assertEqual(imported["project"]["baseline_id"], by_original[first["id"]]["id"])
        self.assertEqual(projects.import_bundle(bundle, "same.json", prepare_source_documents)["duplicates"], [imported["project"]["id"]])
