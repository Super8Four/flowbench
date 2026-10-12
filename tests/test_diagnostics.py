"""Safe diagnostic fixtures and strictly loopback-only native probe coverage."""
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.diagnostics import (DiagnosticsCapture, diagnostic_capabilities, health_delta,
                             latency_summary, linux_snapshot, parse_ping_rtt, probe_target,
                             validate_diagnostics)
from app.route import capture_route
import test_imports as fixtures


def settings(**changes):
    result = {"role": "client", "target": "127.0.0.1", "ip_version": "auto", "port": 5201,
              "protocol": "tcp", "latency": True, "endpoint_health": False, "mtu": False}
    result.update(changes)
    return result


def reply(value, phase="loaded"):
    return {"timestamp": "2026-01-01T00:00:00+00:00", "elapsed_seconds": 0 if phase == "loaded" else None,
            "status": "reply", "rtt_ms": value, "rtt_upper_bound_ms": None}


class DiagnosticsTests(unittest.TestCase):
    def test_parser_preserves_numeric_bounds_and_unparsed_localized_output(self):
        self.assertEqual(parse_ping_rtt("64 bytes time=0.123 ms"), (.123, None))
        self.assertEqual(parse_ping_rtt("Reply time<1ms TTL=128"), (None, 1))
        self.assertEqual(parse_ping_rtt("temps=2,5 ms"), (2.5, None))
        self.assertEqual(parse_ping_rtt("Request timed out."), (None, None))

    def test_variability_uses_only_measured_numeric_replies(self):
        samples = [reply(10), reply(20), {"status": "no_reply", "rtt_ms": None},
                   {"status": "reply", "rtt_ms": None, "rtt_upper_bound_ms": 1}]
        summary = latency_summary(samples)
        self.assertEqual((summary["replies"], summary["missing"], summary["numeric_samples"]), (3, 1, 2))
        self.assertEqual((summary["mean_ms"], summary["stddev_ms"], summary["p95_ms"]), (15, 5, 20))
        self.assertIsNone(latency_summary([])["mean_ms"])

    def test_failed_echo_phase_never_reports_successful_capture(self):
        capture = DiagnosticsCapture(settings())
        with patch.object(capture, "_probe", return_value={"status": "timeout", "rtt_ms": None}):
            result = capture.baseline()
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["summary"]["missing"], 3)
        self.assertIsNone(result["summary"]["mean_ms"])

    def test_host_validation_rejects_commands_and_options(self):
        for target in ("-c", "127.0.0.1;whoami", "a b", "$(whoami)", "a..b", None):
            self.assertIsNone(probe_target(target))
        for target in ("127.0.0.1", "::1", "fe80::1%eth0", "example.invalid"):
            self.assertEqual(probe_target(target), target)

    def test_platform_commands_are_single_bounded_native_probes(self):
        capture = DiagnosticsCapture(settings())
        with patch("app.diagnostics.os.name", "nt"), patch("app.diagnostics.shutil.which", return_value="ping.exe"):
            self.assertEqual(capture._ping_command(1500), ["ping.exe", "-4", "-n", "1", "-w", "900", "-f", "-l", "1472", "127.0.0.1"])
            self.assertIsNone(DiagnosticsCapture(settings(target="::1"))._ping_command(1500))
        with patch("app.diagnostics.os.name", "posix"), patch("app.diagnostics.shutil.which", return_value="/bin/ping"):
            command = capture._ping_command(1400)
            self.assertIn("do", command)
            self.assertIn("1372", command)
            self.assertEqual(command[-1], "127.0.0.1")

    def test_no_opt_in_never_starts_processes_and_finish_is_idempotent(self):
        capture = DiagnosticsCapture(settings(latency=False))
        with patch("app.diagnostics.subprocess.Popen") as launch:
            capture.baseline(); capture.start(); result = capture.finish()
            self.assertIs(result, capture.finish())
        launch.assert_not_called()
        self.assertEqual(result["latency"]["loaded"]["status"], "disabled")
        self.assertEqual(result["endpoint_health"]["remote"]["status"], "unavailable")
        self.assertIs(validate_diagnostics(result), result)

    def test_running_checkpoint_is_portable_and_never_probes(self):
        capture = DiagnosticsCapture(settings(mtu=True))
        capture.baseline_samples = [reply(3, "baseline")]
        capture.baseline_status = "captured"
        capture.loaded_samples = [reply(7)]
        capture.started = time.monotonic()
        with patch("app.diagnostics.subprocess.Popen") as launch:
            result = capture.snapshot()
        launch.assert_not_called()
        self.assertEqual(result["latency"]["loaded"]["status"], "running")
        self.assertEqual(result["path_context"]["mtu"]["status"], "pending")
        validate_diagnostics(result)

    def test_failed_adapter_preserves_partial_samples_in_canonical_evidence(self):
        capture = DiagnosticsCapture(settings())
        capture.baseline_samples = [reply(1, "baseline")]
        result = capture.failure(RuntimeError("native adapter failed"))
        self.assertEqual(result["latency"]["baseline"]["summary"]["mean_ms"], 1)
        self.assertEqual(result["latency"]["loaded"]["status"], "error")
        self.assertEqual(result["endpoint_health"]["remote"]["status"], "unavailable")
        self.assertEqual(capture.processes, set())
        validate_diagnostics(result)

    def test_loaded_adapter_exception_is_evidence_not_silent_thread_loss(self):
        capture = DiagnosticsCapture(settings())
        with patch.object(capture, "_probe", side_effect=RuntimeError("probe failed")):
            capture.start(); capture.worker.join(timeout=1)
            result = capture.finish()
        self.assertEqual(result["latency"]["loaded"]["status"], "error")
        self.assertEqual(result["latency"]["loaded"]["samples"][0]["status"], "error")
        validate_diagnostics(result)

    def test_baseline_loaded_and_after_checks_are_distinct_and_bounded(self):
        capture = DiagnosticsCapture(settings(mtu=True, endpoint_health=True))
        snapshot = {"status": "captured", "cpu": {"total_ticks": 100, "idle_ticks": 50}, "interfaces": []}
        with patch.object(capture, "_probe", side_effect=lambda phase, size=None: reply(3 if phase == "baseline" else 8, phase)), patch.object(capture, "_health", return_value=snapshot), patch("app.diagnostics.MAX_LOADED_SAMPLES", 2), patch("app.diagnostics.PROBE_INTERVAL", .001):
            baseline = capture.baseline()
            self.assertEqual(len(baseline["samples"]), 3)
            capture.start(); capture.worker.join(timeout=1)
            result = capture.finish()
        self.assertEqual(result["latency"]["mean_increase_ms"], 5)
        self.assertEqual(len(result["latency"]["loaded"]["samples"]), 2)
        self.assertEqual(result["path_context"]["mtu"]["largest_confirmed_packet_bytes"], 1500)
        self.assertIsNotNone(result["endpoint_health"]["local"]["before"])
        validate_diagnostics(result)

    def test_loaded_limit_and_cancel_prevent_more_probes(self):
        capture = DiagnosticsCapture(settings())
        with patch.object(capture, "_probe", return_value=reply(1)) as probe, patch("app.diagnostics.MAX_LOADED_SAMPLES", 3), patch("app.diagnostics.PROBE_INTERVAL", 0):
            capture.start(); capture.worker.join(timeout=1)
            self.assertEqual(probe.call_count, 3)
        capture.cancel()
        result = capture.finish()
        self.assertEqual(result["latency"]["loaded"]["status"], "cancelled")
        fresh = DiagnosticsCapture(settings(), cancelled=lambda: True)
        with patch("app.diagnostics.subprocess.Popen") as launch:
            fresh.baseline(); fresh.start(); fresh.finish()
        launch.assert_not_called()

    def test_command_cancel_kills_owned_child_and_keeps_partial_output(self):
        cancel = threading.Event()
        capture = DiagnosticsCapture(settings(), cancelled=cancel.is_set)
        process = Mock(returncode=-1)
        def communicate(timeout):
            if process.kill.called:
                return ("partial output", None)
            cancel.set()
            raise subprocess.TimeoutExpired("ping", timeout)
        process.communicate.side_effect = communicate
        with patch("app.diagnostics.subprocess.Popen", return_value=process) as launch:
            result = capture._command(["ping", "127.0.0.1"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["output"], "partial output")
        process.kill.assert_called_once()
        self.assertEqual(capture.processes, set())
        self.assertNotIn("shell", launch.call_args.kwargs)

    def test_timeout_and_permission_failure_remain_honest(self):
        capture = DiagnosticsCapture(settings())
        process = Mock(returncode=-1)
        process.communicate.return_value = ("Request timed out.", None)
        with patch("app.diagnostics.subprocess.Popen", return_value=process), patch("app.diagnostics.time.monotonic", side_effect=[0, 2]):
            result = capture._command(["ping", "127.0.0.1"], timeout=1)
        self.assertEqual(result["status"], "timeout")
        with patch.object(capture, "_ping_command", return_value=["ping"]), patch.object(capture, "_command", return_value={"status": "completed", "exit_code": 2, "output": "Operation not permitted"}):
            sample = capture._probe("baseline")
        self.assertEqual(sample["status"], "permission_blocked")
        self.assertIsNone(sample["rtt_ms"])

    def test_mtu_missing_replies_do_not_invent_path_size(self):
        capture = DiagnosticsCapture(settings(mtu=True, latency=False))
        with patch.object(capture, "_ping_command", return_value=["ping"]), patch.object(capture, "_probe", return_value={"status": "no_reply", "rtt_ms": None}) as probe:
            result = capture.finish()
        mtu = result["path_context"]["mtu"]
        self.assertEqual(mtu["status"], "unconfirmed")
        self.assertIsNone(mtu["largest_confirmed_packet_bytes"])
        self.assertEqual(probe.call_count, 3)
        self.assertEqual([sample["packet_bytes"] for sample in mtu["tested_packet_sizes"]], [1500, 1400, 1280])

    def test_local_proc_health_and_counter_reset_are_not_remote_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "net").mkdir()
            (root / "stat").write_text("cpu 10 0 10 70 10 0 0 0 0 0\n")
            (root / "net" / "dev").write_text("header\nheader\n lo: 10 1 2 3 0 0 0 0 20 1 4 5 0 0 0 0\n")
            before = linux_snapshot(root)
            self.assertEqual(before["status"], "captured")
            after = copy.deepcopy(before)
            after["cpu"] = {"total_ticks": 200, "idle_ticks": 120}
            after["interfaces"][0].update(rx_errors=5, rx_drops=1)
            delta = health_delta(before, after)
        self.assertEqual(delta["cpu_utilization_percent"], 60)
        self.assertEqual(delta["interfaces"][0]["counters"]["rx_errors"], 3)
        self.assertIsNone(delta["interfaces"][0]["counters"]["rx_drops"])
        self.assertEqual(delta["interfaces"][0]["status"], "partial")

    def test_windows_local_health_adapter_is_fixed_read_only_and_handles_missing(self):
        capture = DiagnosticsCapture(settings(endpoint_health=True))
        data = {"cpu": [10, 30], "interfaces": [{"name": "Loopback", "rx_errors": 0}]}
        with patch("app.diagnostics.os.name", "nt"), patch("app.diagnostics.shutil.which", return_value="powershell.exe"), patch.object(capture, "_command", return_value={"status": "completed", "exit_code": 0, "output": json.dumps(data)}) as command:
            result = capture._health()
        self.assertEqual(result["cpu"]["load_percent"], 20)
        script = command.call_args.args[0][-1]
        self.assertNotIn("ComputerName", script)
        self.assertNotIn("127.0.0.1", script)
        self.assertEqual(command.call_args.kwargs["timeout"], 2)
        with patch("app.diagnostics.os.name", "nt"), patch("app.diagnostics.shutil.which", return_value=None):
            self.assertEqual(capture._health()["status"], "unavailable")

    def test_import_rejects_invented_nonfinite_oversized_and_wrong_summary(self):
        valid = DiagnosticsCapture(settings(latency=False)).finish()
        bad = copy.deepcopy(valid); bad["latency"]["loaded"]["samples"] = [reply(float("nan"))]
        with self.assertRaises(ValueError): validate_diagnostics(bad)
        bad = copy.deepcopy(valid); bad["path_context"]["note"] = "a" * 4097
        with self.assertRaises(ValueError): validate_diagnostics(bad)
        bad = copy.deepcopy(valid); bad["latency"]["loaded"]["samples"] = [reply(1)] * 121
        with self.assertRaises(ValueError): validate_diagnostics(bad)
        bad = copy.deepcopy(valid); bad["latency"]["loaded"]["summary"]["mean_ms"] = 42
        with self.assertRaises(ValueError): validate_diagnostics(bad)
        bad = copy.deepcopy(valid); bad["schema_version"] = 2
        with self.assertRaises(ValueError): validate_diagnostics(bad)

    def test_route_transient_poll_timeout_then_reply_is_not_overall_timeout(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.side_effect = [subprocess.TimeoutExpired("tracepath", .1), ("1: 127.0.0.1 0.1ms", None)]
        with patch("app.route.route_command", return_value=["tracepath", "127.0.0.1"]):
            result = capture_route(settings(), lambda _: process, lambda: False)
        self.assertEqual(result["status"], "captured")
        process.kill.assert_not_called()

    def test_capabilities_only_detect_tools_without_launching_or_installing(self):
        with patch("app.diagnostics.subprocess.Popen") as launch, patch("app.diagnostics.shutil.which", return_value=None):
            result = diagnostic_capabilities()
        launch.assert_not_called()
        self.assertFalse(result["latency"]["available"])
        self.assertFalse(result["route"]["available"])

    @unittest.skipUnless(shutil.which("ping"), "Native ping is unavailable")
    def test_native_ping_loopback_only_and_cancellation_stop(self):
        # No fixture, DNS or external address: the only destination is loopback.
        capture = DiagnosticsCapture(settings())
        baseline = capture.baseline()
        capture.start()
        time.sleep(.15)
        capture.cancel()
        result = capture.finish()
        self.assertFalse(capture.worker.is_alive())
        self.assertEqual(capture.processes, set())
        self.assertEqual(len(baseline["samples"]), 3)
        self.assertTrue(all(sample["status"] in {"reply", "no_reply", "timeout", "unavailable", "permission_blocked"} for sample in baseline["samples"]))
        self.assertEqual(result["latency"]["loaded"]["status"], "cancelled")
        validate_diagnostics(result)


class DiagnosticRunnerTests(unittest.TestCase):
    setUp = fixtures.ImportTests.setUp
    tearDown = fixtures.ImportTests.tearDown

    def traffic_process(self):
        process = Mock(returncode=0)
        process.stdout = io.StringIO(json.dumps({"end": fixtures.saved_run()["summary"]}) + "\n")
        process.stderr = io.StringIO("")
        process.wait.return_value = 0
        process.poll.return_value = 0
        self.manager.capabilities["available"] = True
        return process

    def test_diagnostic_adapter_failure_does_not_fail_measured_traffic(self):
        process = self.traffic_process()
        with patch("app.runner.subprocess.Popen", return_value=process), patch("app.diagnostics.DiagnosticsCapture.baseline", side_effect=RuntimeError("native adapter failed")):
            record = self.manager.start({"target": "127.0.0.1", "mode": "upload", "latency": True})
            self.manager.worker.join(timeout=5)
        result = self.manager.export(record["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["upload_mbps"], 100)
        self.assertEqual(result["diagnostics"]["latency"]["loaded"]["status"], "error")
        validate_diagnostics(result["diagnostics"])

    def test_loaded_probe_checkpoints_measured_samples_before_traffic_finishes(self):
        process = self.traffic_process()
        captured = threading.Event()
        original_save = self.manager.save
        evidence = []
        def save(record):
            original_save(record)
            diagnostics = record.get("diagnostics")
            if diagnostics and diagnostics["latency"]["loaded"]["samples"]:
                evidence.append(self.manager.storage.read(record["id"])["diagnostics"])
                captured.set()
        process.wait.side_effect = lambda timeout: 0 if captured.wait(3) else self.fail("No diagnostic checkpoint")
        with patch("app.runner.subprocess.Popen", return_value=process), patch.object(self.manager, "save", side_effect=save), patch("app.diagnostics.DiagnosticsCapture._probe", side_effect=lambda phase, size=None: reply(2, phase)):
            record = self.manager.start({"target": "127.0.0.1", "mode": "upload", "latency": True})
            self.manager.worker.join(timeout=5)
        self.assertFalse(self.manager.worker.is_alive())
        self.assertTrue(evidence)
        self.assertEqual(evidence[0]["latency"]["loaded"]["status"], "running")
        self.assertEqual(len(evidence[0]["latency"]["baseline"]["samples"]), 3)
        validate_diagnostics(evidence[0])
        self.assertEqual(self.manager.read(record["id"])["status"], "completed")

    def test_checkpoint_storage_failure_stops_traffic_and_rolls_back_checkpoint(self):
        process = self.traffic_process()
        process.poll.return_value = None
        failed = threading.Event()
        original_save = self.manager.save
        old_evidence = []
        def save(record):
            diagnostics = record.get("diagnostics")
            if not failed.is_set() and diagnostics and diagnostics["latency"]["loaded"]["samples"]:
                old_evidence.append(self.manager.storage.read(record["id"])["diagnostics"])
                failed.set()
                raise OSError("fixture disk full")
            return original_save(record)
        process.wait.side_effect = lambda timeout: 0 if failed.wait(3) else self.fail("No storage failure exercised")
        with patch("app.runner.subprocess.Popen", return_value=process), patch.object(self.manager, "save", side_effect=save), patch("app.diagnostics.DiagnosticsCapture._probe", side_effect=lambda phase, size=None: reply(2, phase)):
            record = self.manager.start({"target": "127.0.0.1", "mode": "upload", "latency": True})
            self.manager.worker.join(timeout=5)
        self.assertFalse(self.manager.worker.is_alive())
        self.assertEqual(old_evidence[0]["latency"]["loaded"]["samples"], [])
        self.assertEqual(self.manager.storage_error, "fixture disk full")
        process.kill.assert_called()
        result = self.manager.export(record["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("archive write failed", result["error"])
        validate_diagnostics(result["diagnostics"])


if __name__ == "__main__":
    unittest.main()
