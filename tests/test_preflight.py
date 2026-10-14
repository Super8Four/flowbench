"""Once-per-test preparation, cancellation, persistence and passive provenance."""
import copy
import gzip
import io
import json
import math
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app.preflight import (PreflightCapture, initial_preflight, route_placeholder,
                           validate_preflight, validate_route)
from app.route import capture_route, parse_hops
from app.time_quality import time_quality_result
from app.runner import RunManager
import test_imports as fixtures
import test_flent as flent_fixtures
from test_protocols import process_for


def settings(**changes):
    return {"role": "client", "target": "127.0.0.1", "ip_version": "auto", "port": 5201, "traceroute": False, **changes}


def route_result(config, status="captured"):
    route = route_placeholder(config, status)
    route.update(started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:00:00+00:00", elapsed_seconds=0.0,
                 note="Synthetic loopback preflight", hops=parse_hops("1: 127.0.0.1 0.1ms") if status == "captured" else [])
    return route


def reported_clock():
    value = time_quality_result("status_only")
    value.update(provider="systemd", synchronized=True, command=["timedatectl", "show", "--property=NTPSynchronized"], raw_output="NTPSynchronized=yes")
    return value


class PreflightTests(unittest.TestCase):
    def test_one_route_and_local_time_query_ordered_with_three_checkpoints(self):
        capture = PreflightCapture(settings())
        stages, checkpoints = [], []
        def route(config, start, cancelled):
            stages.append("route")
            return route_result(config)
        def clock(**kwargs):
            stages.append("time")
            self.assertEqual(kwargs["timeout"], 3)
            return reported_clock()
        with patch("app.preflight.capture_route", side_effect=route) as trace, patch("app.time_quality.capture_time_quality", side_effect=clock) as query:
            result = capture.capture(lambda *values: checkpoints.append(copy.deepcopy(values)))
        self.assertEqual(stages, ["route", "time"])
        trace.assert_called_once(); query.assert_called_once()
        self.assertEqual([row[0]["status"] for row in checkpoints], ["pending", "pending", "captured"])
        self.assertEqual(checkpoints[1][1]["status"], "captured")
        validate_preflight(result); validate_route(capture.route)

    def test_cancelled_preflight_does_not_query_clock_or_launch_child(self):
        capture = PreflightCapture(settings(), cancelled=lambda: True)
        with patch("app.preflight.subprocess.Popen") as launch, patch("app.time_quality.capture_time_quality") as query:
            result = capture.capture(lambda *_: None)
        launch.assert_not_called(); query.assert_not_called()
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["time_quality"]["status"], "cancelled")
        self.assertEqual(capture.route["status"], "cancelled")

    def test_adapter_failure_does_not_swallow_checkpoint_storage_failure(self):
        capture = PreflightCapture(settings())
        with patch("app.preflight.capture_route", side_effect=RuntimeError("route adapter")), patch("app.time_quality.capture_time_quality", side_effect=RuntimeError("clock adapter")):
            result = capture.capture(lambda *_: None)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(capture.route["status"], "error")
        self.assertEqual(result["time_quality"]["status"], "error")
        validate_preflight(result)
        with patch("app.preflight.capture_route") as route:
            with self.assertRaisesRegex(OSError, "disk full"):
                PreflightCapture(settings()).capture(Mock(side_effect=OSError("disk full")))
        route.assert_not_called()

    def test_one_test_server_reads_local_time_without_peer_route(self):
        capture = PreflightCapture(settings(role="server", target=""))
        with patch("app.preflight.capture_route") as route, patch("app.time_quality.capture_time_quality", return_value=reported_clock()) as clock:
            result = capture.capture(lambda *_: None)
        route.assert_not_called(); clock.assert_called_once()
        self.assertEqual(capture.route["status"], "not_applicable")
        self.assertEqual(result["phase"], "before_traffic")
        self.assertIn("client can connect later", result["time_quality"]["note"])
        validate_preflight(result)

    def test_ordered_hops_keep_gaps_alternate_addresses_and_numeric_ip_validation(self):
        hops = parse_hops("3: 2001:db8::1 4.5ms\n1: 127.0.0.1 <1ms\n1: 127.0.0.2 2ms\n4: no reply\n5: 999.999.999.999 3ms\n17: 192.0.2.1 1ms")
        self.assertEqual([hop["hop"] for hop in hops], [1, 2, 3, 4, 5])
        self.assertEqual(hops[0]["addresses"], ["127.0.0.1", "127.0.0.2"])
        self.assertEqual(hops[0]["rtt_ms"], [2])
        self.assertEqual(hops[0]["rtt_upper_bound_ms"], [1])
        self.assertFalse(hops[1]["observed"])
        self.assertTrue(hops[3]["observed"])
        self.assertFalse(hops[3]["responded"])
        self.assertIsNone(hops[4]["address"])
        value = route_result(settings()); value["hops"] = hops
        validate_route(value)

    def test_zero_exit_without_hops_retains_raw_as_incomplete(self):
        child = Mock(returncode=0)
        child.poll.return_value = 0
        child.communicate.return_value = ("unrecognized localized output", None)
        with patch("app.route.route_command", return_value=["tracepath", "-n", "-m", "16", "127.0.0.1"]):
            result = capture_route(settings(), lambda _: child, lambda: False)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["raw_output"], "unrecognized localized output")
        self.assertEqual(result["phase"], "before_traffic")
        self.assertGreaterEqual(result["elapsed_seconds"], 0)

    def test_native_output_limit_is_bytes_and_truncation_is_explicit(self):
        child = Mock(returncode=0)
        child.poll.return_value = 0
        child.communicate.return_value = ("\u6e2c" * 20000 + "\n1: 127.0.0.1 0.1ms", None)
        with patch("app.route.route_command", return_value=["tracepath", "-n", "-m", "16", "127.0.0.1"]):
            result = capture_route(settings(), lambda _: child, lambda: False)
        self.assertLessEqual(len(result["raw_output"].encode("utf-8")), 32768)
        self.assertTrue(result["output_truncated"])
        self.assertEqual(result["hops"][0]["address"], "127.0.0.1")
        validate_route(result)

    def test_offline_enrichment_once_and_adapter_error_preserves_route_evidence(self):
        capture = PreflightCapture(settings())
        route = route_result(settings())
        route["hops"] = parse_hops("1: 10.0.0.1 0.1ms")  # Synthetic RFC1918 reply; no probe runs.
        with patch("app.preflight.capture_route", return_value=route), patch("app.time_quality.capture_time_quality", return_value=time_quality_result()), patch("app.geolocation.enrich_route", side_effect=RuntimeError("fixture reader failure")) as enrich:
            result = capture.capture(lambda *_: None)
        enrich.assert_called_once()
        self.assertEqual(capture.route["status"], "captured")
        self.assertEqual(capture.route["hops"][0]["locations"][0]["status"], "internal")
        self.assertEqual(capture.route["geolocation"]["status"], "error")
        validate_route(capture.route); validate_preflight(result)

    def test_modern_and_legacy_route_validation_rejects_bad_metadata(self):
        validate_route({"status": "legacy-status", "hops": [{"hop": 1, "address": None, "rtt_ms": []}]})
        good = route_result(settings())
        for field, bad in (("phase", []), ("status", {}), ("elapsed_seconds", 10 ** 400), ("elapsed_seconds", math.inf),
                           ("command", ["sh", "-c", "whoami"]), ("command", ["tracepath", "-n", "-m", "17", "127.0.0.1"])):
            value = copy.deepcopy(good); value[field] = bad
            with self.subTest(field=field, bad_type=type(bad).__name__), self.assertRaises(ValueError):
                validate_route(value)
        good = initial_preflight()
        for field, bad in (("phase", []), ("status", {}), ("elapsed_seconds", 10 ** 400), ("elapsed_seconds", math.nan), ("started_at", "2026-01-01T00:00:00")):
            value = copy.deepcopy(good); value[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_preflight(value)


class PreflightRunnerTests(unittest.TestCase):
    setUp = fixtures.ImportTests.setUp
    tearDown = fixtures.ImportTests.tearDown

    def ready(self):
        self.manager.capabilities.update(available=True, json_stream=True, bidir=True, udp_bidir=True)

    def test_mandatory_route_time_checkpoint_precedes_traffic_even_legacy_false(self):
        self.ready()
        stages = []
        def route(config, *_):
            stages.append("route")
            return route_result(config)
        def clock(**_):
            stages.append("time")
            return time_quality_result()
        def traffic(*_, **__):
            stages.append("traffic")
            saved = self.manager.storage.read(self.manager.record["id"])
            self.assertEqual(saved["route"]["phase"], "before_traffic")
            self.assertIsNotNone(saved["preflight"]["finished_at"])
            self.assertEqual(saved["preflight"]["time_quality"]["status"], "unavailable")
            return process_for("tcp")
        with patch("app.preflight.capture_route", side_effect=route) as trace, patch("app.time_quality.capture_time_quality", side_effect=clock), patch("app.runner.subprocess.Popen", side_effect=traffic):
            record = self.manager.start({"target": "127.0.0.1", "duration": 1, "mode": "upload", "traceroute": False})
            self.manager.worker.join(5)
        self.assertEqual(stages, ["route", "time", "traffic"])
        trace.assert_called_once()
        result = self.manager.export(record["id"])
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["settings"]["traceroute"])
        validate_preflight(result["preflight"])

    def test_both_and_matrix_once_per_started_child_not_parent(self):
        self.ready()
        for matrix in (False, True):
            count = 4 if matrix else 2
            protocols = ["tcp", "tcp", "udp", "udp"] if matrix else ["tcp", "udp"]
            with patch("app.preflight.capture_route", side_effect=lambda config, *_: route_result(config)) as trace, patch("app.time_quality.capture_time_quality", side_effect=lambda **_: time_quality_result()) as clock, patch("app.runner.subprocess.Popen", side_effect=[process_for(protocol) for protocol in protocols]) as traffic:
                record = (self.manager.start_matrix({"settings": {"target": "127.0.0.1", "duration": 1, "bandwidth": "2M"}, "protocols": ["tcp", "udp"], "modes": ["upload", "download"], "streams": [1]}) if matrix else self.manager.start({"target": "127.0.0.1", "protocol": "both", "mode": "upload", "duration": 1, "bandwidth": "2M"}))
                self.manager.worker.join(8)
            self.assertFalse(self.manager.worker.is_alive())
            self.assertEqual((trace.call_count, clock.call_count, traffic.call_count), (count, count, count))
            result = self.manager.export(record["id"])
            self.assertNotIn("preflight", result)
            for child in result["runs"]:
                validate_preflight(child["preflight"])
                self.assertEqual(child["route"]["phase"], "before_traffic")

    def test_cancel_in_preflight_skips_remaining_children_without_traffic(self):
        self.ready()
        def route(config, *_):
            self.manager.stop(self.manager.active_id)
            return route_result(config, "cancelled")
        with patch("app.preflight.capture_route", side_effect=route) as trace, patch("app.time_quality.capture_time_quality") as clock, patch("app.runner.subprocess.Popen") as traffic:
            record = self.manager.start({"target": "127.0.0.1", "protocol": "both", "mode": "upload", "duration": 1, "bandwidth": "2M"})
            self.manager.worker.join(5)
        traffic.assert_not_called(); clock.assert_not_called(); trace.assert_called_once()
        result = self.manager.export(record["id"])
        self.assertEqual([child["status"] for child in result["runs"]], ["cancelled", "skipped"])
        self.assertNotIn("preflight", result["runs"][1])
        self.assertEqual(result["runs"][0]["preflight"]["status"], "cancelled")

    def test_checkpoint_disk_failure_prevents_traffic_and_restores_prior_evidence(self):
        self.ready()
        original = self.manager.save
        previous = []
        def save(record):
            if record.get("route", {}).get("status") == "captured":
                previous.append(self.manager.storage.read(record["id"])["route"]["status"])
                raise OSError("fixture checkpoint disk full")
            return original(record)
        with patch.object(self.manager, "save", side_effect=save), patch("app.preflight.capture_route", side_effect=lambda config, *_: route_result(config)), patch("app.time_quality.capture_time_quality") as clock, patch("app.runner.subprocess.Popen") as traffic:
            record = self.manager.start({"target": "127.0.0.1", "mode": "upload"})
            self.manager.worker.join(5)
        traffic.assert_not_called(); clock.assert_not_called()
        self.assertEqual(previous, ["pending"])
        self.assertEqual(self.manager.storage_error, "fixture checkpoint disk full")
        row = self.manager.read(record["id"])
        self.assertEqual(row["route"]["status"], "pending")
        self.assertEqual(row["status"], "failed")

    def test_restart_retains_route_and_marks_missing_pending_time_honestly(self):
        row = fixtures.saved_run(); row["status"] = "starting"; row["finished_at"] = None
        row["preflight"] = initial_preflight(); row["route"] = route_result(settings())
        self.manager.save(row)
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        restored = self.manager.read(row["id"])
        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(restored["route"], row["route"])
        self.assertEqual(restored["preflight"]["status"], "partial")
        self.assertEqual(restored["preflight"]["time_quality"]["status"], "unavailable")
        self.assertIsNone(restored["preflight"]["elapsed_seconds"])
        validate_preflight(restored["preflight"])

    def test_flent_uses_same_preflight_before_actual_workload_process(self):
        self.manager.flent_capabilities = {"available": True, "version": "Flent 2.3.0", "dependencies": {"flent": "/fixture/flent"}}
        stages = []
        def route(config, *_):
            stages.append("route")
            self.assertIsNone(self.manager.process)
            return route_result(config)
        def launch(*args, **kwargs):
            stages.append("traffic")
            self.assertTrue(kwargs["start_new_session"])
            saved = self.manager.storage.read(self.manager.record["id"])
            self.assertIsNotNone(saved["preflight"]["finished_at"])
            with gzip.open(Path(kwargs["cwd"]) / "native.flent.gz", "wt", encoding="utf-8") as handle:
                json.dump(flent_fixtures.native(), handle)
            child = Mock(pid=321, returncode=0, stdout=io.StringIO("Native Flent fixture\n"), stderr=io.StringIO())
            child.poll.return_value = 0; child.wait.return_value = 0
            return child
        with patch("app.preflight.capture_route", side_effect=route) as trace, patch("app.time_quality.capture_time_quality", side_effect=lambda **_: stages.append("time") or time_quality_result()) as clock, patch("app.runner.subprocess.Popen", side_effect=launch) as process, patch("app.flent.stop_process"):
            record = self.manager.start_flent(flent_fixtures.payload())
            self.manager.worker.join(5)
        self.assertEqual(stages, ["route", "time", "traffic"])
        trace.assert_called_once(); clock.assert_called_once(); process.assert_called_once()
        self.assertEqual(self.manager.read(record["id"])["status"], "completed")

    def test_cancelled_flent_preflight_never_uses_flent_process_group(self):
        self.manager.flent_capabilities = {"available": True, "version": "Flent 2.3.0", "dependencies": {"flent": "/fixture/flent"}}
        def route(config, *_):
            self.manager.stop(self.manager.active_id)
            return route_result(config, "cancelled")
        with patch("app.preflight.capture_route", side_effect=route), patch("app.time_quality.capture_time_quality") as clock, patch("app.runner.subprocess.Popen") as traffic, patch("app.flent.stop_process") as group:
            record = self.manager.start_flent(flent_fixtures.payload())
            self.manager.worker.join(5)
        traffic.assert_not_called(); clock.assert_not_called(); group.assert_not_called()
        self.assertEqual(self.manager.read(record["id"])["status"], "cancelled")

    def incoming_start(self):
        listener = self.manager.listener
        listener.command = ["fixture-iperf", "-s"]
        listener.generation = "fixture-listener"
        return listener, {"accepted_connection": {"host": "127.0.0.1", "port": 12345},
                          "test_start": {"protocol": "TCP", "duration": 1, "num_streams": 1}}

    def test_passive_time_query_does_not_block_or_overwrite_finished_measurements(self):
        listener, start = self.incoming_start()
        release, began = threading.Event(), threading.Event()
        def clock(**_):
            began.set(); release.wait(2)
            return time_quality_result()
        with patch("app.preflight.capture_route") as trace, patch("app.time_quality.capture_time_quality", side_effect=clock) as query:
            with self.manager.lock:
                listener.begin(start)
                run_id = listener.record["id"]
            self.assertTrue(began.wait(1))
            with self.manager.lock:
                listener.record["summary"] = fixtures.saved_run()["summary"]
                listener.log("Synthetic measurement retained")
                listener.finish("completed")
            release.set(); listener.time_worker.join(3)
        trace.assert_not_called(); query.assert_called_once()
        row = self.manager.export(run_id)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["metrics"]["upload_mbps"], 100)
        self.assertEqual(row["event_count"], 1)
        self.assertEqual(row["route"]["status"], "not_applicable")
        self.assertEqual(row["preflight"]["phase"], "observed_after_start")
        self.assertIn("not a before-traffic", row["preflight"]["time_quality"]["note"])
        validate_preflight(row["preflight"])

    def test_rapid_incoming_sessions_do_not_queue_unbounded_clock_jobs(self):
        listener, start = self.incoming_start()
        release = threading.Event()
        def clock(**kwargs):
            release.wait(2)
            return time_quality_result("cancelled" if kwargs["cancelled"]() else "unavailable")
        with patch("app.time_quality.capture_time_quality", side_effect=clock) as query:
            with self.manager.lock:
                listener.begin(start); first = listener.record["id"]
                listener.finish("completed")
                listener.begin(start); second = listener.record["id"]
                listener.finish("completed")
            release.set(); listener.time_worker.join(3)
        query.assert_called_once()
        second_row = self.manager.read(second)
        self.assertEqual(second_row["preflight"]["time_quality"]["status"], "unavailable")
        self.assertIn("No additional query was queued", second_row["preflight"]["time_quality"]["note"])
        self.assertEqual(self.manager.read(first)["preflight"]["time_quality"]["status"], "cancelled")

    def test_stopping_listener_does_not_launch_query_for_buffered_start(self):
        listener, start = self.incoming_start()
        listener.stopping = True
        with patch("app.time_quality.capture_time_quality") as clock:
            with self.manager.lock:
                listener.begin(start)
                run_id = listener.record["id"]
                listener.finish("cancelled")
        clock.assert_not_called()
        row = self.manager.read(run_id)
        self.assertEqual(row["preflight"]["time_quality"]["status"], "cancelled")
        self.assertIsNone(listener.time_worker)

    def test_shutdown_retains_writer_ownership_until_exited_monitor_finishes(self):
        listener = self.manager.listener
        monitor = Mock()
        monitor.is_alive.return_value = True
        listener.worker = monitor
        try:
            self.manager.shutdown()
            self.assertFalse(self.manager.storage_lock.closed)
            self.assertIn("writer ownership is retained", self.manager.storage_error)
            self.assertEqual(monitor.join.call_count, 2)
        finally:
            listener.worker = None

    def test_passive_clock_archive_failure_keeps_old_checkpoint_and_fails_readiness(self):
        listener, start = self.incoming_start()
        release, began = threading.Event(), threading.Event()
        def clock(**_):
            began.set(); release.wait(2)
            return time_quality_result()
        with patch("app.time_quality.capture_time_quality", side_effect=clock):
            with self.manager.lock:
                listener.begin(start)
                run_id = listener.record["id"]
            self.assertTrue(began.wait(1))
            original = self.manager.save
            def save(row):
                if row.get("preflight", {}).get("finished_at") is not None:
                    raise OSError("fixture passive disk full")
                return original(row)
            with patch.object(self.manager, "save", side_effect=save):
                release.set(); listener.time_worker.join(3)
        self.assertEqual(self.manager.storage_error, "fixture passive disk full")
        self.assertEqual(listener.state, "failed")
        self.assertIsNone(self.manager.storage.read(run_id)["preflight"]["finished_at"])
        with self.manager.lock:
            listener.finish("failed")


if __name__ == "__main__":
    unittest.main()
