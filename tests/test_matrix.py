import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.matrix import matrix_plan
from app.exports import xml_export
from app.imports import import_runs
from app.runner import CREATE_FLAGS, RunManager, settings_from
from test_protocols import process_for


def request(**changes):
    return {"settings": {"target": "127.0.0.1", "duration": 1, "bandwidth": "2M"},
            "protocols": ["tcp", "udp"], "modes": ["upload", "download"], "streams": [1, 2], **changes}


def wait_for_matrix_capture(manager, *, idle_timeout=10, total_timeout=40, clock=None, wait=None):
    """Wait for mocked children to persist, bounding stalls and total runtime.

    Eight children commit 48 events plus their lifecycle writes. A controlled
    .22-second delay per durable event left the worker alive after ten seconds
    (41 committed events), then all children completed at 11.625 seconds. This
    demonstrates a fixed-join failure, not the cause of a particular CI failure.
    Sample under the capture lock so in-flight writes do not count as progress;
    acquiring that lock is itself bounded and cannot hide a stalled worker.
    """
    worker = manager.worker
    clock, wait = clock or time.monotonic, wait or worker.join
    began = last_progress_at = clock()
    previous, state = None, {"capture_lock_observed": False}
    while True:
        observed = clock()
        remaining = min(total_timeout - (observed - began), idle_timeout - (observed - last_progress_at))
        if remaining > 0 and manager.lock.acquire(timeout=min(.05, remaining)):
            try:
                record, job = manager.record or {}, manager.job or {}
                progress = (job.get("status"), record.get("id"), record.get("status"),
                            record.get("event_count", 0), record.get("finished_at"))
                state = {"capture_lock_observed": True, "job_id": job.get("id"), "job_status": job.get("status"),
                         "current": {key: record.get(key) for key in ("id", "status", "event_count", "finished_at", "error")},
                         "interval_count": len(record.get("intervals", [])),
                         "summary_keys": sorted(record.get("summary", {})),
                         "capture_errors": list(manager.capture_errors), "storage_error": manager.storage_error}
            finally:
                manager.lock.release()
            observed = clock()
            if progress != previous:
                previous, last_progress_at = progress, observed
        else:
            observed = clock()
            state["capture_lock_observed"] = False
        alive = worker.is_alive()
        if observed - began >= total_timeout:
            reason = "overall_deadline"
        elif not alive:
            return
        elif observed - last_progress_at >= idle_timeout:
            reason = "no_capture_progress"
        else:
            wait(min(.05, total_timeout - (observed - began), idle_timeout - (observed - last_progress_at)))
            continue
        state.update(reason=reason, worker_alive=alive, elapsed_seconds=round(observed - began, 3),
                     idle_seconds=round(observed - last_progress_at, 3), idle_timeout=idle_timeout, total_timeout=total_timeout)
        raise AssertionError("Matrix evidence did not finish: " + json.dumps(state, sort_keys=True))


class MatrixCaptureWaitTests(unittest.TestCase):
    def fixture(self, evolve, complete_at=float("inf")):
        class Clock:
            value = 0.0
            def now(self):
                return self.value
            def wait(self, seconds):
                self.value += max(seconds, .000001)
                evolve(self.value, manager)
        clock = Clock()
        manager = Mock(lock=threading.RLock(), record={"id": "child", "status": "running", "event_count": 1},
                       job={"id": "matrix", "status": "running"}, capture_errors=[], storage_error=None)
        manager.worker.is_alive.side_effect = lambda: clock.value < complete_at
        return manager, clock

    def test_durable_capture_progress_allows_completion_after_ten_seconds(self):
        manager, clock = self.fixture(lambda seconds, manager: manager.record.update(event_count=1 + int(seconds / 3)), complete_at=12)
        wait_for_matrix_capture(manager, clock=clock.now, wait=clock.wait)
        self.assertGreaterEqual(clock.value, 12)
        self.assertLess(clock.value, 13)

    def test_metadata_changes_do_not_hide_a_ten_second_capture_stall(self):
        manager, clock = self.fixture(lambda seconds, manager: manager.job.update(updated_at=seconds))
        with self.assertRaisesRegex(AssertionError, '"reason": "no_capture_progress"') as raised:
            wait_for_matrix_capture(manager, clock=clock.now, wait=clock.wait)
        self.assertGreaterEqual(clock.value, 10)
        self.assertLess(clock.value, 11)
        self.assertIn('"event_count": 1', str(raised.exception))

    def test_continuing_events_cannot_extend_the_overall_deadline(self):
        manager, clock = self.fixture(lambda seconds, manager: manager.record.update(event_count=1 + int(seconds)))
        with self.assertRaisesRegex(AssertionError, '"reason": "overall_deadline"'):
            wait_for_matrix_capture(manager, clock=clock.now, wait=clock.wait)
        self.assertGreaterEqual(clock.value, 40)
        self.assertLess(clock.value, 41)

    def test_busy_capture_lock_cannot_hide_a_stall_or_block_the_wait(self):
        manager, clock = self.fixture(lambda seconds, manager: None)
        timeouts = []
        def unavailable(timeout):
            timeouts.append(timeout)
            clock.value += timeout
            return False
        manager.lock = Mock()
        manager.lock.acquire.side_effect = unavailable
        with self.assertRaisesRegex(AssertionError, '"reason": "no_capture_progress"'):
            wait_for_matrix_capture(manager, clock=clock.now, wait=clock.wait)
        self.assertGreaterEqual(clock.value, 10)
        self.assertLess(clock.value, 11)
        self.assertLessEqual(max(timeouts), .05)
        manager.lock.release.assert_not_called()


class MatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.manager.capabilities.update(available=True, bidir=True, udp_bidir=True, json_stream=True)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def run_matrix(self, data=None, codes=None):
        data = data or request()
        _, combinations, _ = matrix_plan(data, settings_from)
        codes = codes or [0] * len(combinations)
        with patch.object(self.manager, "preflight", return_value=True), patch("app.runner.subprocess.Popen", side_effect=[process_for(row["protocol"], code)
                                                              for row, code in zip(combinations, codes)]) as launch:
            record = self.manager.start_matrix(data)
            wait_for_matrix_capture(self.manager)
        self.assertFalse(self.manager.worker.is_alive())
        return self.manager.export(record["id"]), launch

    def test_matrix_order_sequential_identity_settings_metrics_restart_and_slot(self):
        original = self.manager.run
        def verify_reserved(grouped=False):
            with self.assertRaises(RuntimeError):
                self.manager.start({"target": "127.0.0.1"})
            with self.assertRaises(RuntimeError):
                self.manager.start_matrix(request())
            original(grouped)
        with patch.object(self.manager, "run", side_effect=verify_reserved):
            job, launch = self.run_matrix()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["kind"], "matrix-job")
        self.assertEqual(job["matrix"]["planned_seconds"], 8)
        self.assertEqual(launch.call_count, 8)
        expected = [(protocol, mode, streams) for protocol in ["tcp", "udp"]
                    for mode in ["upload", "download"] for streams in [1, 2]]
        self.assertEqual([(r["settings"]["protocol"], r["settings"]["mode"], r["settings"]["streams"])
                          for r in job["runs"]], expected)
        for index, child in enumerate(job["runs"], 1):
            self.assertEqual(child["matrix_job"], {"id": job["id"], "order": index})
            self.assertTrue(child["events"])
            self.assertLessEqual(child["created_at"], child["started_at"])
            self.assertEqual(child["created_at"], job["created_at"])
            if index > 1:
                self.assertLessEqual(job["runs"][index - 2]["finished_at"], child["started_at"])
        self.assertEqual(self.manager.read_events(job["id"]), [])
        self.assertEqual(len(self.manager.list_runs()), 9)
        self.assertIsNone(self.manager.active_id)
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.assertEqual(self.manager.export(job["id"]), job)

    def test_invalid_limits_fail_before_write(self):
        invalid = [None, {}, request(protocols=[]), request(protocols=["tcp", "tcp"]),
                   request(protocols=["both"]), request(modes=["up"]), request(streams=[True]),
                   request(streams=["1"]), request(streams=[0]), request(streams=[9]),
                   request(streams=[1, 1]), request(modes=["upload", "download", "bidir"], streams=[1, 2, 3]),
                   request(settings={"target": "127.0.0.1", "role": "server"}),
                   request(settings={"target": "127.0.0.1", "duration": 61}),
                   request(settings={"target": "127.0.0.1", "duration": 60})]
        with patch.object(self.manager, "preflight", return_value=True), patch("app.runner.subprocess.Popen") as launch:
            for data in invalid:
                with self.subTest(data=data), self.assertRaises(ValueError):
                    self.manager.start_matrix(data)
            launch.assert_not_called()
        self.assertEqual(self.manager.list_runs(), [])

    def test_udp_finite_aggregate_limit_includes_streams_and_directions(self):
        for bandwidth, mode, streams in [("0", "upload", 1), ("0.000", "upload", 1),
                                         ("100M", "bidir", 1), ("50M", "bidir", 2),
                                         ("100.001M", "upload", 1), ("1G", "upload", 1)]:
            data = request(settings={"target": "127.0.0.1", "duration": 1, "bandwidth": bandwidth},
                           protocols=["udp"], modes=[mode], streams=[streams])
            with self.subTest(bandwidth=bandwidth, mode=mode, streams=streams), self.assertRaises(ValueError):
                self.manager.start_matrix(data)
        for bandwidth, mode, streams in [("50M", "bidir", 1), ("12.5M", "upload", 8),
                                         ("100000K", "upload", 1)]:
            _, rows, _ = matrix_plan(request(settings={"target": "127.0.0.1", "duration": 1, "bandwidth": bandwidth},
                                            protocols=["udp"], modes=[mode], streams=[streams]), settings_from)
            self.assertEqual(rows[0]["bandwidth"], bandwidth)
        self.assertEqual(self.manager.list_runs(), [])

    def test_unsupported_capability_fail_before_any_insert(self):
        self.manager.capabilities["udp_bidir"] = False
        with self.assertRaises(ValueError):
            self.manager.start_matrix(request(modes=["upload", "bidir"]))
        self.assertEqual(self.manager.list_runs(), [])

    def test_parent_creation_transaction_rollback(self):
        original = self.manager.storage.create
        calls = []
        def fail(row, connection=None):
            calls.append(row["id"])
            if len(calls) == 4:
                raise OSError("Fixture disk failure")
            return original(row, connection)
        with patch.object(self.manager.storage, "create", side_effect=fail), patch.object(self.manager, "preflight", return_value=True), patch("app.runner.subprocess.Popen") as launch:
            with self.assertRaises(OSError):
                self.manager.start_matrix(request())
            launch.assert_not_called()
        self.assertEqual(self.manager.list_runs(), [])
        self.assertIsNone(self.manager.active_id)

    def test_partial_children_errors_and_complete_failure_retained(self):
        for codes, expected in [([0, 1], "partial"), ([1, 1], "failed")]:
            job, launch = self.run_matrix(request(protocols=["tcp"], streams=[1]), codes)
            self.assertEqual(job["status"], expected)
            self.assertEqual([r["status"] for r in job["runs"]], ["completed" if code == 0 else "failed" for code in codes])
            self.assertEqual(launch.call_count, 2)
            self.assertTrue(job["error"])

    def test_cancel_between_tests_skips_all_queued_without_launch(self):
        original = self.manager.run
        def stop_after_first(grouped=False):
            original(grouped)
            self.manager.stop(self.manager.active_id)
        with patch.object(self.manager, "run", side_effect=stop_after_first):
            job, launch = self.run_matrix()
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual([r["status"] for r in job["runs"]], ["completed"] + ["skipped"] * 7)
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(all(not r["events"] for r in job["runs"][1:]))

    def test_cancel_active_test_terminates_and_skips_queue(self):
        process = process_for("tcp")
        process.poll.return_value = None
        def stop_wait(timeout=None):
            self.manager.stop(self.manager.active_id)
            return -1
        process.wait.side_effect = stop_wait
        with patch.object(self.manager, "preflight", return_value=True), patch("app.runner.subprocess.Popen", return_value=process) as launch:
            record = self.manager.start_matrix(request())
            self.manager.worker.join(10)
        job = self.manager.export(record["id"])
        self.assertEqual([r["status"] for r in job["runs"]], ["cancelled"] + ["skipped"] * 7)
        self.assertEqual(launch.call_count, 1)
        process.terminate.assert_called_once()

    def test_restart_interrupts_parent_children_without_auto_resume(self):
        with patch.object(self.manager, "run_job"):
            record = self.manager.start_matrix(request())
            self.manager.worker.join(5)
        self.manager.active = None
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        job = self.manager.export(record["id"])
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual({r["status"] for r in job["runs"]}, {"interrupted"})
        self.assertIsNone(self.manager.worker)

    def test_rollback_legacy_preserves_matrix_identity(self):
        job, _ = self.run_matrix()
        directory = Path(self.temp.name) / "legacy-copy"
        self.manager.storage.export_legacy(directory)
        restored = RunManager(directory, "missing-iperf")
        try:
            self.assertEqual(restored.export(job["id"]), job)
        finally:
            restored.shutdown()

    def test_cancel_diagnostics_baseline_prevents_any_traffic(self):
        capture = Mock()
        capture.finish.return_value = {"status": "cancelled"}
        capture.snapshot.return_value = {"status": "cancelled"}
        def cancel_baseline():
            self.manager.stop(self.manager.active_id)
        capture.baseline.side_effect = cancel_baseline
        data = request(settings={"target": "127.0.0.1", "duration": 1, "bandwidth": "2M", "latency": True})
        with patch("app.diagnostics.DiagnosticsCapture", return_value=capture), patch.object(self.manager, "preflight", return_value=True), patch("app.runner.subprocess.Popen") as launch:
            job = self.manager.start_matrix(data)
            self.manager.worker.join(10)
        result = self.manager.export(job["id"])
        launch.assert_not_called()
        capture.start.assert_not_called()
        capture.cancel.assert_called_once()
        capture.finish.assert_called_once()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([r["status"] for r in result["runs"]], ["cancelled"] + ["skipped"] * 7)
        self.assertEqual(result["runs"][0]["diagnostics"], {"status": "cancelled"})
        self.assertIsNone(self.manager.diagnostics_capture)

    def test_diagnostics_separate_per_child_and_finish_on_launch_failure(self):
        captures = [Mock(), Mock()]
        for index, capture in enumerate(captures):
            capture.finish.return_value = {"fixture": index}
            capture.snapshot.return_value = {"fixture": index}
        data = request(settings={"target": "127.0.0.1", "duration": 1, "bandwidth": "2M", "endpoint_health": True},
                       protocols=["tcp"], streams=[1])
        with patch("app.diagnostics.DiagnosticsCapture", side_effect=captures), patch.object(self.manager, "preflight", return_value=True), patch("app.runner.subprocess.Popen", side_effect=[process_for("tcp"), OSError("Fixture launch failure")]):
            job = self.manager.start_matrix(data)
            self.manager.worker.join(10)
        result = self.manager.export(job["id"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual([r["diagnostics"] for r in result["runs"]], [{"fixture": 0}, {"fixture": 1}])
        for capture in captures:
            capture.baseline.assert_called_once()
            capture.finish.assert_called_once()
        captures[0].start.assert_called_once()
        captures[1].start.assert_not_called()
        self.assertIsNone(self.manager.process)
        self.assertIsNone(self.manager.diagnostics_capture)

    def test_json_xml_import_references_deduplication_and_atomic_rejection(self):
        job, _ = self.run_matrix(request(protocols=["tcp"], streams=[1]), [0, 1])
        result = import_runs(self.manager, copy.deepcopy(job), "matrix.json")
        restored = self.manager.export(result["imported"][0])
        self.assertEqual(restored["status"], "partial")
        for original, child in zip(job["runs"], restored["runs"]):
            self.assertEqual(child["matrix_job"]["id"], restored["id"])
            for key in ("settings", "summary", "events", "created_at", "finished_at"):
                self.assertEqual(child[key], original[key])
        from app.imports import parse_document
        # The XML format must carry exactly the same source identity/evidence.
        parsed = parse_document(xml_export(job))
        duplicate = import_runs(self.manager, parsed, "matrix.xml")
        self.assertEqual(duplicate["duplicates"][0], restored["id"])
        count = len(self.manager.list_runs())
        for alteration in ("reference", "mode", "status", "order"):
            broken = copy.deepcopy(job)
            if alteration == "reference":
                broken["children"][0]["id"] = "a" * 32
            elif alteration == "mode":
                broken["runs"][0]["settings"]["mode"] = "download"
            elif alteration == "status":
                broken["status"] = "completed"
            else:
                broken["runs"][0]["matrix_job"]["order"] = 2
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                import_runs(self.manager, broken, "broken.json")
        self.assertEqual(len(self.manager.list_runs()), count)


@unittest.skipUnless(RunManager.find_executable(), "Install iPerf3 for loopback matrix tests.")
class RealMatrixTests(unittest.TestCase):
    def test_loopback_sequential_tcp_udp_upload_download(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            with socket.socket() as socket_handle:
                socket_handle.bind(("127.0.0.1", 0))
                port = socket_handle.getsockname()[1]
            peer = subprocess.Popen([manager.executable, "-s", "-B", "127.0.0.1", "-p", str(port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_FLAGS)
            try:
                time.sleep(.4)
                data = request(settings={"target": "127.0.0.1", "port": port, "duration": 1, "bandwidth": "1M"}, streams=[1])
                job = manager.start_matrix(data)
                # Four children each retain their bounded route/clock preflight
                # before the one-second traffic measurement.
                manager.worker.join(120)
                self.assertFalse(manager.worker.is_alive())
                result = manager.export(job["id"])
                self.assertEqual(result["status"], "completed", result["error"])
                self.assertEqual(len(result["runs"]), 4)
                for child in result["runs"]:
                    self.assertGreater(child["metrics"][child["settings"]["mode"] + "_mbps"], 0)
            finally:
                manager.shutdown()
                peer.terminate()
                peer.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
