import copy
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.flent import RRUL_TCP, RRUL_RTT, capabilities, command, normalize, read_result, settings, unprivileged_icmp, validate_flent
from app.imports import import_runs, parse_document
from app.exports import xml_export
from app.runner import RunManager


def native():
    data = {"version": 4, "metadata": {"NAME": "rrul", "HOST": "127.0.0.1", "LENGTH": 1},
            "x_values": [0, 1, 2], "results": {"TCP upload BE": [1, None, 3],
            "TCP download BE": [2, 4, 6], "Ping (ms) ICMP": [None, .5, 1.5]},
            "raw_values": {"TCP upload BE": [{"t": 1, "val": 1}]}}
    for name in sorted(RRUL_TCP):
        data["results"].setdefault(name, [1, None, 3])
    for name in sorted(RRUL_RTT):
        data["results"].setdefault(name, [None, .5, 1.5])
    return data


def payload(**changes):
    return {"target": "127.0.0.1", "duration": 1, "netserver_port": 12865,
            "workload": "rrul", "confirm_netserver": True, **changes}


class FlentEvidenceTests(unittest.TestCase):
    def test_native_data_identity_null_gaps_units_and_summaries(self):
        source = native()
        result = normalize(source, "Flent 2.3.0")
        self.assertEqual(result["data"], source)
        self.assertEqual([r["unit"] for r in result["series"]][:3], ["Mbits/s", "Mbits/s", "ms"])
        self.assertEqual(result["series"][0]["points"][1], {"seconds": 1, "value": None})
        self.assertEqual(result["summaries"][0], {"name": "TCP upload BE", "unit": "Mbits/s", "count": 2,
                                                   "missing": 1, "mean": 2, "min": 1, "max": 3})
        validate_flent(result)
        source["results"]["Additional"] = [None, None, None]
        row = normalize(source)["summaries"][-1]
        self.assertEqual((row["unit"], row["count"], row["mean"]), (None, 0, None))

    def test_missing_native_tcp_does_not_claim_complete_and_measured_zero_is_valid(self):
        source = native()
        for name in RRUL_TCP:
            source["results"][name] = [None, None, None]
        evidence = normalize(source)
        self.assertEqual(evidence["status"], "partial")
        self.assertEqual(evidence["quality"]["missing_tcp_series"], sorted(RRUL_TCP))
        self.assertFalse(evidence["quality"]["complete"])
        self.assertEqual(evidence["data"], source)
        evidence["status"] = "captured"
        with self.assertRaises(ValueError): validate_flent(evidence)
        for name in RRUL_TCP:
            source["results"][name] = [0, None, 0]
        zero = normalize(source)
        self.assertEqual(zero["status"], "captured")
        self.assertTrue(zero["quality"]["complete"])

    def test_captured_status_requires_native_source_even_without_derived_rows(self):
        evidence = {"tool": "Flent", "workload": "rrul", "status": "captured", "data": None,
                    "series": [], "summaries": []}
        with self.assertRaises(ValueError): validate_flent(evidence)
        evidence["status"] = "partial"
        with self.assertRaises(ValueError): validate_flent(evidence)
        evidence["status"] = "missing"
        validate_flent(evidence)

    def test_malformed_native_and_fabricated_derived_values_rejected(self):
        for alteration in ("test", "time", "length", "numeric", "decreasing", "infinity"):
            bad = native()
            if alteration == "test": bad["metadata"]["NAME"] = "udp_flood"
            elif alteration == "time": bad["x_values"][0] = True
            elif alteration == "length": bad["results"]["TCP upload BE"].append(4)
            elif alteration == "numeric": bad["results"]["TCP upload BE"][0] = "1"
            elif alteration == "decreasing": bad["x_values"] = [2, 1, 0]
            else: bad["results"]["TCP upload BE"][0] = float("inf")
            with self.subTest(alteration=alteration), self.assertRaises(ValueError): normalize(bad)
        record = normalize(native())
        record["summaries"][0]["mean"] = 100
        with self.assertRaises(ValueError): validate_flent(record)

    def test_bounded_settings_argument_safety_confirmation(self):
        for changes in ({"confirm_netserver": False}, {"confirm_netserver": "true"}, {"target": "host;id"},
                        {"workload": "udp_flood"}, {"duration": 61}, {"duration": True}, {"netserver_port": 0},
                        {"netserver_port": "12865;id"}, {"latency": True}, {"bind": "127.0.0.1;id"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): settings(payload(**changes))
        validated = settings(payload(duration=60, label="Owned test"))
        self.assertEqual(validated["flent_workload"], "rrul")
        with tempfile.TemporaryDirectory() as directory:
            args = command({"dependencies": {"flent": "/tool/flent"}}, validated, directory)
            self.assertEqual(args[:4], ["/tool/flent", "rrul", "-H", "127.0.0.1"])
            self.assertEqual(args[args.index("-l") + 1], "60")
            self.assertNotIn("--remote-metadata", args)
            self.assertNotIn("-x", args)
            self.assertTrue((Path(directory) / "empty.flentrc").is_file())

    def test_bind_is_validated_saved_and_passed_to_control_and_data_flows(self):
        configured = settings(payload(bind="127.0.0.1", ip_version="4"))
        self.assertEqual(configured["bind"], "127.0.0.1")
        with tempfile.TemporaryDirectory() as directory:
            args = command({"dependencies": {"flent": "/tool/flent"}}, configured, directory)
        self.assertIn("-4", args)
        self.assertEqual(args[args.index("--local-bind") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("--control-local-bind") + 1], "127.0.0.1")

    def test_native_declared_units_preserved_and_ambiguous_names_not_inferred(self):
        source = native()
        source["results"]["TCP totals"] = [1, None, 3]
        source["results"]["TCP upload unknown"] = [1, None, 3]
        source["results"]["Native extra"] = [1, None, 3]
        source["metadata"]["SERIES_META"] = {"Native extra": {"UNITS": "bytes/s"}}
        units = {row["name"]: row["unit"] for row in normalize(source)["series"]}
        self.assertEqual(units["TCP totals"], "Mbits/s")
        self.assertIsNone(units["TCP upload unknown"])
        self.assertIsNone(units["Native extra"])
        self.assertEqual(normalize(source)["data"]["metadata"]["SERIES_META"]["Native extra"]["UNITS"], "bytes/s")

    def test_compressed_native_file_and_symlink_ambiguous_missing_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.flent.gz"
            with self.assertRaises(ValueError): read_result(directory)
            with gzip.open(path, "wt", encoding="utf-8") as handle: json.dump(native(), handle)
            self.assertEqual(read_result(directory)["data"], native())
            (Path(directory) / "second.json").write_text(json.dumps(native()))
            with self.assertRaises(ValueError): read_result(directory)

    def test_native_windows_unavailable_and_linux_missing_dependencies(self):
        with patch("app.flent.sys.platform", "win32"), patch("app.flent.shutil.which", return_value="tool"):
            self.assertFalse(capabilities()["available"])
        with patch("app.flent.sys.platform", "linux"), patch("app.flent.shutil.which", return_value=None):
            self.assertFalse(capabilities()["available"])

    def test_detected_cli_versions_are_harmless_and_explicit(self):
        replies = [Mock(returncode=0, stdout="fping Version 5\n", stderr=""),
                   Mock(returncode=0, stdout="Flent 2.3.0\n", stderr=""),
                   Mock(returncode=0, stdout="Netperf version 2.7.1\n", stderr="")]
        with patch("app.flent.sys.platform", "linux"), patch("app.flent.unprivileged_icmp", return_value={"available": True}), patch("app.flent.shutil.which", side_effect=lambda name: "/tools/" + name), patch("app.flent.subprocess.run", side_effect=replies) as probe:
            detected = capabilities()
        self.assertTrue(detected["available"])
        self.assertEqual(detected["version"], "Flent 2.3.0")
        self.assertEqual(detected["netperf_version"], "Netperf version 2.7.1")
        self.assertEqual([call.args[0] for call in probe.call_args_list], [["/tools/fping", "-v"], ["/tools/flent", "--version"], ["/tools/netperf", "-V"]])

    def test_blocked_unprivileged_socket_reports_unavailable_without_network(self):
        with patch("app.flent.socket.socket", side_effect=PermissionError("Fixture kernel permission denied")) as create_socket:
            result = unprivileged_icmp()
        self.assertFalse(result["available"])
        self.assertIn("kernel", result["error"])
        create_socket.assert_called_once()
        with patch("app.flent.sys.platform", "linux"), patch("app.flent.unprivileged_icmp", return_value=result), patch("app.flent.shutil.which", return_value="tool"), patch("app.flent.subprocess.run") as probe:
            detected = capabilities()
        self.assertFalse(detected["available"])
        probe.assert_not_called()

    def test_ping_exec_permission_error_reports_unavailable(self):
        with patch("app.flent.sys.platform", "linux"), patch("app.flent.unprivileged_icmp", return_value={"available": True}), patch("app.flent.shutil.which", return_value="tool"), patch("app.flent.subprocess.run", side_effect=PermissionError("Fixture ping EPERM")):
            result = capabilities()
        self.assertFalse(result["available"])
        self.assertIn("EPERM", result["error"])


class FlentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.manager.flent_capabilities = {"available": True, "version": "Flent 2.3.0", "dependencies": {"flent": "/tool/flent"}}

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def fixture(self, code=0, write=True, cancel=False, data=None):
        def launch(*args, **kwargs):
            if write:
                with gzip.open(Path(kwargs["cwd"]) / "native.flent.gz", "wt", encoding="utf-8") as handle:
                    json.dump(data or native(), handle)
            process = Mock(pid=123, stdout=io.StringIO("Flent output retained\n"), stderr=io.StringIO())
            process.poll.return_value = None if cancel else code
            if cancel:
                def wait(timeout=None):
                    self.manager.stop(self.manager.active_id)
                    return -15
                process.wait.side_effect = wait
            else:
                process.wait.return_value = code
            return process
        return launch

    def run_fixture(self, **kwargs):
        with patch("app.runner.subprocess.Popen", side_effect=self.fixture(**kwargs)), patch("app.flent.stop_process") as stop:
            record = self.manager.start_flent(payload())
            self.manager.worker.join(10)
        self.assertFalse(self.manager.worker.is_alive())
        return self.manager.export(record["id"]), stop

    def test_actual_tool_record_evidence_rawlogs_archive_and_restart(self):
        record, stop = self.run_fixture()
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["kind"], "flent-run")
        self.assertEqual(record["flent"]["data"], native())
        self.assertNotIn("upload_mbps", record["metrics"])
        self.assertNotIn("download_mbps", record["metrics"])
        if record["metrics"]:
            self.assertEqual(record["metrics"]["flent_TCP upload BE_mbps"], 2)
        self.assertEqual(record["raw_stdout"], "Flent output retained")
        self.assertIsNone(self.manager.active_id)
        self.assertFalse(list(Path(self.temp.name).glob("flent-*")))
        stop.assert_called()
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.assertEqual(self.manager.export(record["id"]), record)

    def test_failed_missing_and_cancelled_evidence_honest(self):
        for kwargs, status, evidence in [({"code": 1}, "failed", "partial"),
                                          ({"write": False}, "failed", "missing"),
                                          ({"cancel": True}, "cancelled", "partial")]:
            with self.subTest(kwargs=kwargs):
                record, stop = self.run_fixture(**kwargs)
                self.assertEqual(record["status"], status)
                self.assertEqual(record["flent"]["status"], evidence)
                self.assertTrue(any(call.kwargs.get("force") for call in stop.call_args_list))

    def test_zero_cli_exit_with_missing_tcp_is_partial_and_keeps_raw_data(self):
        source = native()
        for name in RRUL_TCP:
            source["results"][name] = [None, None, None]
        record, _ = self.run_fixture(data=source)
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["status"], "partial")
        self.assertEqual(record["flent"]["status"], "partial")
        self.assertEqual(record["flent"]["data"], source)
        self.assertIn("incomplete", record["error"])

    def test_unavailable_invalid_and_disk_failure_before_launch(self):
        with patch("app.runner.subprocess.Popen") as launch:
            self.manager.flent_capabilities["available"] = False
            with self.assertRaises(RuntimeError): self.manager.start_flent(payload())
            self.manager.flent_capabilities["available"] = True
            with self.assertRaises(ValueError): self.manager.start_flent(payload(confirm_netserver=False))
            with patch.object(self.manager, "save", side_effect=OSError("Fixture disk error")):
                with self.assertRaises(OSError): self.manager.start_flent(payload())
            launch.assert_not_called()
        self.assertEqual(self.manager.list_runs(), [])

    def test_restart_snapshot_does_not_resume_flent(self):
        with patch.object(self.manager, "run_flent"):
            record = self.manager.start_flent(payload())
            self.manager.worker.join(5)
        self.manager.active = None
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.assertEqual(self.manager.read(record["id"])["status"], "interrupted")
        self.assertIsNone(self.manager.worker)

    def test_json_xml_import_preserves_flent_provenance_and_native_data(self):
        record, _ = self.run_fixture()
        imported = import_runs(self.manager, record, "flent.json")
        restored = self.manager.export(imported["imported"][0])
        self.assertEqual(restored["flent"], record["flent"])
        self.assertEqual(restored["settings"], record["settings"])
        duplicate = import_runs(self.manager, parse_document(xml_export(record)), "flent.xml")
        self.assertEqual(duplicate["duplicates"][0], restored["id"])
        broken = copy.deepcopy(record)
        broken["flent"]["series"][0]["points"][0]["value"] = 999
        with self.assertRaises(ValueError): import_runs(self.manager, broken, "fabricated.json")

    def test_stalled_shutdown_retains_writer_and_disables_new_traffic(self):
        worker = Mock()
        worker.is_alive.return_value = True
        self.manager.worker = worker
        self.manager.shutdown()
        worker.join.assert_called_once_with(timeout=15)
        self.assertFalse(self.manager.storage_lock.closed)
        self.assertTrue(self.manager.shutdown_requested)
        self.assertIn("ownership is retained", self.manager.storage_error)
        with self.assertRaises(RuntimeError):
            RunManager(self.temp.name, "missing-iperf")
        with self.assertRaises(RuntimeError):
            self.manager.start_flent(payload())
        # The worker exits later; a second cleanup can safely release ownership.
        worker.is_alive.return_value = False
        self.manager.shutdown()
        self.assertTrue(self.manager.storage_lock.closed)


if __name__ == "__main__":
    unittest.main()
