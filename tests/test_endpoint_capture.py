"""Actual owned loopback socket evidence; no external target or live archive."""

import copy
import errno
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import unittest
from unittest.mock import patch

from app import create_app
from app.runner import CREATE_FLAGS, RunManager
try:
    import test_app as existing
    from test_listener import wait_for_incoming_capture
    from test_matrix import wait_for_matrix_capture
except ModuleNotFoundError:
    from tests import test_app as existing
    from tests.test_listener import wait_for_incoming_capture
    from tests.test_matrix import wait_for_matrix_capture


IPERF = RunManager.find_executable()


def streamed_starts(events):
    result = []
    for event in events:
        try:
            value = json.loads(event["message"])
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("event") == "start" and isinstance(value.get("data"), dict):
            result.append(value["data"])
    return result


@unittest.skipUnless(IPERF, "Install iPerf3 for actual loopback endpoint capture checks.")
class EndpointCaptureTests(unittest.TestCase):
    setUp = existing.RealIperfTests.setUp
    tearDown = existing.RealIperfTests.tearDown
    port = existing.RealIperfTests.port

    def peer(self, family=4):
        host = "127.0.0.1" if family == 4 else "::1"
        if family == 4:
            port = self.port()
        else:
            if not socket.has_ipv6:
                self.skipTest("This host has no IPv6 socket support.")
            try:
                with socket.socket(socket.AF_INET6) as candidate:
                    candidate.bind((host, 0))
                    port = candidate.getsockname()[1]
            except OSError as exc:
                self.skipTest("Owned IPv6 loopback bind unavailable: " + str(exc))
        peer = subprocess.Popen([IPERF, "-s", "-1", "-J", "-B", host, "-p", str(port), "-" + str(family)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, creationflags=CREATE_FLAGS)
        self.peers.append(peer)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if peer.poll() is not None:
                error = peer.communicate(timeout=2)[1]
                if family == 6 and any(text in error.lower() for text in ("address family not supported", "protocol not supported")):
                    self.skipTest("Native iPerf IPv6 unavailable: " + error.strip())
                self.fail("Owned iPerf peer exited before listening: " + error)
            with socket.socket(socket.AF_INET if family == 4 else socket.AF_INET6) as probe:
                try:
                    probe.bind((host, port))
                except OSError as exc:
                    if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
                        return host, port
                    raise
            time.sleep(.05)
        self.fail("Owned iPerf peer did not bind within five seconds.")

    def assert_points(self, row, native, protocol, observer, family):
        self.assertEqual(row["source"], "iperf_start_connected")
        self.assertEqual(row["observer"], observer)
        self.assertEqual(row["protocol"], protocol)
        self.assertEqual(row["socket_id"], native["socket"])
        for side in ("local", "remote"):
            point = row[side]
            self.assertEqual(point["address"], str(ipaddress.ip_address(native[side + "_host"])))
            self.assertEqual(point["port"], native[side + "_port"])
            self.assertEqual(point["family"], family)
            self.assertEqual(ipaddress.ip_address(point["address"]).version, family)
            self.assertIs(type(point["port"]), int)
            self.assertGreater(point["port"], 0)

    def assert_native_sockets(self, record, start, reporter, protocol, observer, family):
        native_rows = start.get("connected", [])
        self.assertTrue(native_rows, start)
        rows = [row for row in record["endpoints"]["sockets"] if row["reporter"] == reporter]
        self.assertEqual(len(rows), len(native_rows), record["endpoints"])
        for native in native_rows:
            matches = [row for row in rows if row["socket_id"] == native["socket"]]
            self.assertEqual(len(matches), 1)
            self.assert_points(matches[0], native, protocol, observer, family)
        return rows

    def observe_commits(self, snapshots):
        append = self.manager.storage.append
        def observed(record, event):
            append(record, event)
            starts = streamed_starts([event])
            if starts and starts[0].get("connected"):
                saved = self.manager.storage.read(record["id"])
                saved_events = self.manager.storage.events(record["id"])
                snapshots.append((copy.deepcopy(saved), starts[0], event))
                self.assertEqual(saved["endpoints"], record["endpoints"])
                self.assertEqual(saved_events[-1], event)
        return observed

    def capture(self, protocol="tcp", mode="upload", streams=1, family=4):
        host, port = self.peer(family)
        snapshots = []
        with patch.object(self.manager, "preflight", return_value=True), patch.object(self.manager.storage, "append", side_effect=self.observe_commits(snapshots)):
            response = self.client.post("/api/runs", json={"target": host, "port": port, "bind": host,
                "protocol": protocol, "mode": mode, "streams": streams, "duration": 1,
                "bandwidth": "2M", "ip_version": str(family)}, environ_overrides={"REMOTE_ADDR": "127.0.0.2"})
            self.assertEqual(response.status_code, 201, response.get_json())
            wait_for_matrix_capture(self.manager)
        record = self.manager.export(response.get_json()["id"])
        self.assertEqual(record["status"], "completed", record.get("error"))
        self.assertEqual(self.manager.capture_errors, [])
        self.assertTrue(snapshots, "Structured native start must be persisted with its original event.")
        start = snapshots[0][1]
        local = self.assert_native_sockets(record, start, "local", protocol, "client", family)
        self.assertGreaterEqual(len(local), streams)
        self.assertTrue(all(row["remote"]["port"] == port for row in local))
        self.assertTrue(all(row["local"]["port"] != port for row in local))
        self.assertEqual(record["initiator_context"]["peer"], {"address": "127.0.0.2", "port": None, "family": 4})
        self.assertTrue(all(row["local"]["address"] != "127.0.0.2" for row in local))
        self.assertEqual(record["endpoints"]["execution_hostname"], socket.gethostname())
        self.assertIn(record["endpoints"]["status"], {"captured", "partial"})
        self.assertEqual(record["endpoints"]["destinations"], [])
        for saved, native, event in snapshots:
            self.assert_native_sockets(saved, native, "local", protocol, "client", family)
            self.assertEqual(saved["event_count"], event["sequence"])
        native = record.get("native_result") or {}
        peer = native.get("server_output_json") or record.get("server_output_json")
        if isinstance(peer, str):
            peer = json.loads(peer)
        self.assertIsInstance(peer, dict, "Owned JSON peer must supply its distinct native evidence.")
        peer_rows = self.assert_native_sockets(record, peer["start"], "peer", protocol, "server", family)
        self.assertTrue(all(row["local"]["port"] == port for row in peer_rows))
        controls = [row for row in record["endpoints"]["control_peers"] if row["reporter"] == "peer"]
        accepted = peer["start"]["accepted_connection"]
        self.assertEqual(len(controls), 1)
        self.assertEqual(record["endpoints"]["control_peers"], controls)
        self.assertEqual(controls[0]["peer"], {"address": str(ipaddress.ip_address(accepted["host"])),
                                               "port": accepted["port"], "family": family})
        self.assertEqual(controls[0]["source"], "iperf_accepted_connection")
        if protocol == "tcp":
            self.assertNotIn(accepted["port"], {row["remote"]["port"] for row in peer_rows})
        # UDP and TCP have separate port namespaces and can legitimately use
        # the same integer. Exact native data-row matching above proves that
        # the TCP control acceptance was not promoted to a UDP data socket.
        # In reverse/bidir modes the native local/remote tuple is never swapped
        # to mimic sender/receiver direction.
        self.assertEqual(len({(row["reporter"], row["socket_id"], json.dumps(row["local"], sort_keys=True), json.dumps(row["remote"], sort_keys=True))
                              for row in record["endpoints"]["sockets"]}), len(record["endpoints"]["sockets"]))
        proof = os.environ.get("ENDPOINT_CAPTURE_PROOF_DIR")
        if proof:
            Path(proof).mkdir(parents=True, exist_ok=True)
            (Path(proof) / f"{protocol}-{mode}-{streams}-ipv{family}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record

    def test_actual_tcp_upload_reverse_bidir_and_parallel_keep_native_tuple_perspective(self):
        for mode, streams in (("upload", 1), ("download", 1), ("bidir", 1), ("upload", 3)):
            if mode == "bidir" and not self.manager.capabilities["bidir"]:
                continue  # The advertised engine capability controls this mode.
            with self.subTest(mode=mode, streams=streams):
                self.capture(mode=mode, streams=streams)

    def test_actual_udp_supported_directions_keep_data_and_control_ports_separate(self):
        modes = ["upload", "download"] + (["bidir"] if self.manager.capabilities["udp_bidir"] else [])
        for mode in modes:
            with self.subTest(mode=mode):
                self.capture(protocol="udp", mode=mode)

    def test_actual_ipv6_loopback_numeric_family(self):
        self.capture(family=6)

    def test_streaming_capture_survives_end_only_native_and_sqlite_reopen(self):
        record = self.capture()
        from app.endpoints import derive_endpoints
        end_only = copy.deepcopy(record)
        end_only["native_result"] = {"event": "end", "data": record["summary"]}
        end_only.pop("server_output_json", None)
        self.assertEqual(derive_endpoints(end_only), record["endpoints"])
        self.manager.shutdown()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": IPERF})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["iperf_runs"]
        self.assertEqual(self.manager.export(record["id"])["endpoints"], record["endpoints"])
        self.assertEqual(self.manager.export(record["id"])["events"], record["events"])

    def test_failed_connection_retains_no_invented_measured_socket(self):
        # Holding a non-listening owned TCP socket reserves a local destination
        # without accepting any connection or involving an external service.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            with patch.object(self.manager, "preflight", return_value=True):
                response = self.client.post("/api/runs", json={"target": "127.0.0.1", "port": reserved.getsockname()[1],
                    "duration": 1, "mode": "upload", "ip_version": "4"})
                self.assertEqual(response.status_code, 201, response.get_json())
                wait_for_matrix_capture(self.manager)
        record = self.manager.export(response.get_json()["id"])
        proof = os.environ.get("ENDPOINT_CAPTURE_PROOF_DIR")
        if proof:
            Path(proof).mkdir(parents=True, exist_ok=True)
            (Path(proof) / "failed-no-connect.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["endpoints"]["status"], "unavailable")
        self.assertEqual(record["endpoints"]["sockets"], [])
        self.assertEqual(record["endpoints"]["control_peers"], [])
        self.assertEqual(record["endpoints"]["destinations"], [])
        self.assertTrue(record["error"])
        self.assertTrue(record["events"])

    def test_actual_managed_incoming_server_uses_server_namespace_and_no_web_initiator(self):
        listener = self.app.extensions["iperf_listener"]
        port = self.port()
        response = self.client.post("/api/listener", json={"bind": "127.0.0.1", "port": port, "max_duration": 10},
                                    environ_overrides={"REMOTE_ADDR": "127.0.0.2"})
        self.assertEqual(response.status_code, 201, response.get_json())
        time.sleep(.3)
        native = subprocess.run([IPERF, "-c", "127.0.0.1", "-p", str(port), "-t", "1", "-P", "2", "-b", "2M", "-J", "-4"],
                                capture_output=True, text=True, timeout=12, creationflags=CREATE_FLAGS)
        self.assertEqual(native.returncode, 0, native.stdout + native.stderr)
        record = wait_for_incoming_capture(self.manager, listener, 1)[0]
        self.assertEqual(record["status"], "completed", record.get("error"))
        self.assertNotIn("initiator_context", record)
        start = record["native_result"]["start"]
        rows = self.assert_native_sockets(record, start, "local", "tcp", "server", 4)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["local"]["port"] == port for row in rows))
        controls = record["endpoints"]["control_peers"]
        self.assertEqual(len(controls), 1)
        self.assertEqual(controls[0]["reporter"], "local")
        self.assertEqual(controls[0]["source"], "iperf_accepted_connection")
        self.assertEqual(controls[0]["peer"], {"address": str(ipaddress.ip_address(start["accepted_connection"]["host"])),
                                               "port": start["accepted_connection"]["port"], "family": 4})
        self.assertNotIn(controls[0]["peer"]["port"], {row["remote"]["port"] for row in rows})
        listener.stop(disable=True)
        self.assertFalse(listener.worker.is_alive())
        self.manager.shutdown()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": IPERF})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["iperf_runs"]
        self.assertEqual(self.manager.export(record["id"])["endpoints"], record["endpoints"])
