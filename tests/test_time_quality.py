import copy
from datetime import datetime, timezone
import io
import math
import subprocess
import unittest
from unittest.mock import Mock, patch

from app.time_quality import (capture_time_quality, time_quality_result,
                              validate_time_quality, _query)


CHRONY = "CB00710F,203.0.113.15,3,1791979200.5,-0.000006523,0.25,0.000035822,-3.225,-0.000,0.129,0.013639022,0.001100737,64.2,Normal\n"
WINDOWS = """Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0003538s
Root Dispersion: 0.0100002s
ReferenceId: 0xCB00710F (source IP: 203.0.113.15)
Last Successful Sync Time: 5/6/2026 7:51:39 PM
Source: ntp.example.test
Poll Interval: 6 (64s)
Phase Offset: -0.0000013s
ClockRate: 0.0156250s
State Machine: 2 (Sync)
Time Source Flags: 0 (None)
Last Sync Error: 0 (The command completed successfully.)
Time since Last Good Sync Time: 15.7344985s
"""


class TimeQualityTests(unittest.TestCase):
    def capture(self, output, provider="chrony", query_status="ok", **kwargs):
        platform = "win32" if provider == "windows_time" else "linux"
        tool = {"chrony": "chronyc", "windows_time": "w32tm", "systemd": "timedatectl"}[provider]
        path = "/tools/" + tool
        with patch("app.time_quality.sys.platform", platform), patch("app.time_quality.shutil.which", side_effect=lambda name: path if name == tool else None), patch("app.time_quality._query", return_value=(query_status, output)) as query:
            result = capture_time_quality(**kwargs)
        validate_time_quality(result)
        return result, query

    def test_chrony_current_system_correction_not_last_offset_and_utc(self):
        result, query = self.capture(CHRONY)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["provider"], "chrony")
        self.assertTrue(result["synchronized"])
        self.assertEqual(result["source"], "203.0.113.15")
        self.assertEqual(result["offset_seconds"], -.000006523)
        self.assertNotEqual(result["offset_seconds"], .25)
        self.assertEqual(result["root_delay_seconds"], .013639022)
        self.assertEqual(result["uncertainty_seconds"], .001100737)
        self.assertEqual(result["uncertainty_kind"], "root_dispersion_proxy")
        self.assertIn("not guaranteed", result["note"])
        self.assertEqual(result["last_sync_at"], datetime.fromtimestamp(1791979200.5, timezone.utc).isoformat())
        self.assertFalse(result["ntp_verified"])
        self.assertEqual(result["clock_source"], "system_utc")
        self.assertEqual(query.call_args.args[0], ["/tools/chronyc", "-n", "-c", "-h", "127.0.0.1", "tracking"])

    def test_chrony_unsynchronized_does_not_turn_unmeasured_offset_into_zero(self):
        raw = "00000000,0.0.0.0,0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,Not synchronised\n"
        result, _ = self.capture(raw)
        self.assertEqual(result["status"], "unsynchronized")
        self.assertFalse(result["synchronized"])
        self.assertIsNone(result["source"])
        self.assertIsNone(result["offset_seconds"])
        self.assertIsNone(result["uncertainty_seconds"])
        self.assertIsNone(result["last_sync_at"])
        self.assertEqual(result["raw_output"], raw)

    def test_chrony_local_reference_is_not_external_verification(self):
        result, _ = self.capture(CHRONY.replace("CB00710F,203.0.113.15", "7F7F0101,LOCAL"))
        self.assertIn("local reference", result["note"])
        self.assertFalse(result["ntp_verified"])

    def test_negative_zero_is_preserved_and_root_dispersion_zero_valid(self):
        result, _ = self.capture(CHRONY.replace("-0.000006523", "-0.000000000").replace("0.001100737", "0.000000000"))
        self.assertEqual(math.copysign(1, result["offset_seconds"]), -1)
        self.assertEqual(result["uncertainty_seconds"], 0)

    def test_chrony_malformed_csv_nonfinite_units_or_negative_dispersion(self):
        for output in ("bad", CHRONY + CHRONY, CHRONY.replace("0.25", "NaN"),
                       CHRONY.replace("0.25", "1e999"), CHRONY.replace("-0.000006523", "6ms"),
                       CHRONY.replace("0.001100737", "-0.01"), CHRONY.replace("Normal", "unknown"),
                       CHRONY.replace("1791979200.5", "1e200")):
            with self.subTest(output=output):
                result, _ = self.capture(output)
                self.assertEqual(result["status"], "malformed")
                self.assertIsNone(result["offset_seconds"])

    def test_windows_verbose_signed_offset_and_age_only_last_sync(self):
        result, query = self.capture(WINDOWS, "windows_time")
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["offset_seconds"], -.0000013)
        self.assertEqual(result["source"], "ntp.example.test")
        observed = datetime.fromisoformat(result["observed_at"])
        synced = datetime.fromisoformat(result["last_sync_at"])
        self.assertAlmostEqual((observed - synced).total_seconds(), 15.7344985, places=5)
        self.assertFalse(result["ntp_verified"])
        self.assertEqual(query.call_args.args[0], ["/tools/w32tm", "/query", "/status", "/verbose"])
        missing_age = WINDOWS.replace("Time since Last Good Sync Time: 15.7344985s\n", "")
        result, _ = self.capture(missing_age, "windows_time")
        self.assertIsNone(result["last_sync_at"], "An ambiguous local calendar date must not be guessed as UTC")

    def test_windows_unsynchronized_and_local_cmos(self):
        for text in (WINDOWS.replace("Leap Indicator: 0", "Leap Indicator: 3"),
                     WINDOWS.replace("ntp.example.test", "Local CMOS Clock"),
                     WINDOWS.replace("Last Sync Error: 0", "Last Sync Error: 5")):
            result, _ = self.capture(text, "windows_time")
            self.assertEqual(result["status"], "unsynchronized")
            self.assertFalse(result["synchronized"])
            self.assertIsNone(result["offset_seconds"])
            self.assertIsNone(result["uncertainty_seconds"])

    def test_unsupported_windows_locale_is_malformed_not_guessed(self):
        for text in (WINDOWS.replace("Source:", "Quelle:"), WINDOWS.replace("-0.0000013s", "-0,0000013s"),
                     WINDOWS.replace("0.0100002s", "nan"), WINDOWS + "Phase Offset: 0.1s\n",
                     WINDOWS.replace("ntp.example.test", "ntp\x00.example.test")):
            result, _ = self.capture(text, "windows_time")
            self.assertEqual(result["status"], "malformed")
            self.assertIsNone(result["offset_seconds"])

    def test_systemd_reports_only_flag_without_fabricated_details(self):
        for text, expected in (("NTPSynchronized=yes\n", "status_only"), ("NTPSynchronized=no\n", "unsynchronized")):
            result, query = self.capture(text, "systemd")
            self.assertEqual(result["status"], expected)
            for field in ("source", "offset_seconds", "uncertainty_seconds", "last_sync_at", "root_delay_seconds", "root_dispersion_seconds"):
                self.assertIsNone(result[field])
            self.assertEqual(query.call_args.args[0], ["/tools/timedatectl", "show", "--property=NTPSynchronized"])

    def test_linux_fallback_preserves_aggregate_deadline_and_explains_limitation(self):
        with patch("app.time_quality.sys.platform", "linux"), patch("app.time_quality.shutil.which", side_effect=lambda name: "/tools/" + name), patch("app.time_quality._query", side_effect=[("unavailable", "506 Cannot talk to daemon"), ("ok", "NTPSynchronized=yes\n")]) as query:
            result = capture_time_quality()
        self.assertEqual(result["provider"], "systemd")
        self.assertEqual(result["status"], "status_only")
        self.assertIn("Chrony query unavailable", result["note"])
        first, last = [call.args[2] for call in query.call_args_list]
        self.assertGreater(last, first)
        self.assertLessEqual(last - first, 1.2)
        validate_time_quality(result)

    def test_unavailable_permission_timeout_and_cancellation_remain_explicit(self):
        for status in ("unavailable", "permission_blocked", "timeout", "cancelled"):
            result, _ = self.capture("Fixture local query failure", "windows_time", status)
            self.assertEqual(result["status"], status)
            self.assertIsNone(result["synchronized"])
            self.assertIsNone(result["offset_seconds"])
            self.assertFalse(result["ntp_verified"])
        with patch("app.time_quality.subprocess.Popen") as launch:
            result = capture_time_quality(cancelled=lambda: True)
        self.assertEqual(result["status"], "cancelled")
        launch.assert_not_called()

    def test_missing_tools_and_unsupported_platform_do_not_launch_or_write(self):
        with patch("builtins.open", side_effect=AssertionError("No file writes or file reads needed")), patch("app.time_quality.sys.platform", "darwin"), patch("app.time_quality.subprocess.Popen") as launch:
            result = capture_time_quality()
        self.assertEqual(result["status"], "unavailable")
        launch.assert_not_called()
        with patch("app.time_quality.sys.platform", "linux"), patch("app.time_quality.shutil.which", return_value=None), patch("app.time_quality.subprocess.Popen") as launch:
            result = capture_time_quality()
        self.assertEqual(result["provider"], None)
        launch.assert_not_called()

    def test_invalid_budget_and_no_fresh_deadline_per_fallback(self):
        for value in (-1, True, float("inf"), float("nan"), 10**999):
            with self.subTest(value=value), self.assertRaises(ValueError): capture_time_quality(timeout=value)
        with patch("app.time_quality.sys.platform", "win32"), patch("app.time_quality.shutil.which", return_value="/tools/w32tm"), patch("app.time_quality.subprocess.Popen") as launch:
            result = capture_time_quality(timeout=0)
        self.assertEqual(result["status"], "timeout")
        launch.assert_not_called()

    def test_strict_import_validation_and_readonly_command_allowlist(self):
        source, _ = self.capture(CHRONY)
        mutations = [("schema_version", True), ("status", []), ("ntp_verified", True), ("ntp_verified", 0),
                     ("clock_source", "ntp"), ("provider", []), ("offset_seconds", float("inf")),
                     ("synchronized", False), ("status", "status_only"),
                     ("uncertainty_seconds", -1), ("root_dispersion_seconds", float("nan")),
                     ("query_elapsed_seconds", True), ("query_elapsed_seconds", 11), ("source", "x" * 513),
                     ("source", "clock\npretend verified"), ("command", []),
                     ("raw_output", "x" * 8193), ("observed_at", "2026-10-14T01:02:03+01:00"),
                     ("uncertainty_kind", "verified_accuracy"), ("synchronized", 1),
                     ("command", ["chronyc", "makestep"]), ("command", ["chronyc", "-h", "example.test", "tracking"])]
        for key, invalid in mutations:
            value = copy.deepcopy(source)
            value[key] = invalid
            with self.subTest(key=key, invalid=invalid), self.assertRaises(ValueError): validate_time_quality(value)
        value = copy.deepcopy(source)
        value["extra"] = "unsupported"
        with self.assertRaises(ValueError): validate_time_quality(value)
        value = copy.deepcopy(source)
        value.pop("source")
        with self.assertRaises(ValueError): validate_time_quality(value)


class QueryOwnershipTests(unittest.TestCase):
    def setUp(self):
        # Mock Popen PIDs never identify real owned process groups on Linux.
        patcher = patch("app.time_quality.os.killpg", create=True)
        self.kill_group = patcher.start()
        self.addCleanup(patcher.stop)

    def process(self, raw=b"", code=0, running=False):
        process = Mock(pid=123, stdout=io.BytesIO(raw), returncode=code)
        process.poll.return_value = None if running else code
        process.wait.return_value = code
        return process

    def test_query_uses_no_shell_no_stdin_and_drains_bounded_output(self):
        process = self.process(b"NTPSynchronized=yes\n")
        with patch("app.time_quality.subprocess.Popen", return_value=process) as launch:
            status, raw = _query(["/tools/timedatectl", "show", "--property=NTPSynchronized"], lambda: False, float("inf"))
        self.assertEqual(status, "ok")
        self.assertEqual(raw, "NTPSynchronized=yes\n")
        self.assertIs(launch.call_args.kwargs["shell"], False)
        self.assertEqual(launch.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertTrue(process.stdout.closed)

    def test_permission_error_does_not_trigger_install_or_retry(self):
        with patch("app.time_quality.subprocess.Popen", side_effect=PermissionError("Fixture access denied")) as launch:
            status, _ = _query(["/tools/w32tm", "/query", "/status", "/verbose"], lambda: False, float("inf"))
        self.assertEqual(status, "permission_blocked")
        self.assertEqual(launch.call_count, 1)
        process = self.process(b"Access is denied (0x80070005)", code=0)
        with patch("app.time_quality.subprocess.Popen", return_value=process):
            status, _ = _query(["/tools/w32tm", "/query", "/status", "/verbose"], lambda: False, float("inf"))
        self.assertEqual(status, "permission_blocked")

    def test_output_overflow_is_not_parsed_and_closes_pipes(self):
        process = self.process(b"x" * 12000)
        with patch("app.time_quality.subprocess.Popen", return_value=process):
            status, raw = _query(["/tools/chronyc", "tracking"], lambda: False, float("inf"))
        self.assertEqual(status, "malformed")
        self.assertEqual(len(raw), 8192)
        self.assertTrue(process.stdout.closed)

    def test_cancel_and_timeout_kill_only_owned_query(self):
        for cancelled, clock, expected in (([False, True], [0, 0], "cancelled"), ([False, False], [0, 2], "timeout")):
            process = self.process(running=True)
            with patch("app.time_quality.subprocess.Popen", return_value=process), patch("app.time_quality.time.monotonic", side_effect=clock):
                status, _ = _query(["/tools/w32tm", "/query", "/status", "/verbose"], Mock(side_effect=cancelled), 1)
            self.assertEqual(status, expected)
            process.kill.assert_called()
            self.assertTrue(process.stdout.closed)


if __name__ == "__main__":
    unittest.main()
