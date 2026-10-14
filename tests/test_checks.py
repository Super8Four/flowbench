"""Only synthetic DNS messages and disposable loopback peers are used here."""

import copy
from contextlib import contextmanager, ExitStack
import errno
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import socketserver
import struct
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import dns.flags
import dns.message
import dns.rcode
import dns.rrset

from app.checks import (CheckCapture, check_capabilities, check_run_status, initial_result, prepare_check,
                        summarize_check, validate_check_result, validate_check_settings, validate_check_record)
from app.check_worker import dns_attempt, query_dns
from app.time_quality import time_quality_result


class LocalDNS:
    """Owned DNS peer, no recursion, forwarding, network defaults or public traffic."""
    def __init__(self, mode="answer", tcp=False, port=0):
        self.mode, self.queries, self.transports = mode, [], []
        self.release = threading.Event()
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    self.handle_dns()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    # A cancelled owned query can close between TCP accept,
                    # header receipt and reply. It is not a fixture failure.
                    pass

            def handle_dns(self):
                if tcp:
                    header = self.request.recv(2)
                    if len(header) != 2:
                        return
                    length = struct.unpack("!H", header)[0]
                    wire = b""
                    while len(wire) < length:
                        chunk = self.request.recv(length - len(wire))
                        if not chunk:
                            return
                        wire += chunk
                else:
                    wire = self.request[0]
                query = dns.message.from_wire(wire)
                owner.queries.append(query)
                owner.transports.append("tcp" if tcp else "udp")
                if owner.mode == "timeout":
                    return
                if owner.mode == "stall":
                    owner.release.wait(2)
                    return
                response = dns.message.make_response(query)
                if owner.mode == "wrong_id":
                    response.id ^= 1
                if owner.mode == "wrong_question":
                    response.question = dns.message.make_query("different.invalid.", "A").question
                if owner.mode == "nxdomain":
                    response.set_rcode(dns.rcode.NXDOMAIN)
                elif owner.mode == "servfail":
                    response.set_rcode(dns.rcode.SERVFAIL)
                elif owner.mode == "truncated":
                    response.flags |= dns.flags.TC
                elif owner.mode == "answer":
                    response.answer.append(dns.rrset.from_text(query.question[0].name, 37, "IN", "A", "203.0.113.7"))
                wire = response.to_wire()
                if tcp:
                    self.request.sendall(struct.pack("!H", len(wire)) + wire)
                elif owner.mode == "wrong_source":
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unexpected:
                        unexpected.bind(("127.0.0.1", 0))
                        unexpected.sendto(wire, self.client_address)
                else:
                    self.request[1].sendto(wire, self.client_address)
        server_class = socketserver.ThreadingTCPServer if tcp else socketserver.ThreadingUDPServer
        self.server = server_class(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server.server_address[1]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)


@contextmanager
def paired_dns(udp_mode="truncated", tcp_mode="answer"):
    """Hold both loopback binds before starting either protocol's server.

    Windows TCP/UDP ephemeral and excluded-port ranges need not coincide. Let
    TCP choose first, then bind UDP to that held port. After an allocation
    conflict, try independent ephemeral candidates rather than repeatedly
    walking a consecutive range excluded for the other protocol. No socket
    is released and rebound between reservation and use.
    """
    for attempt in range(32):
        tcp = udp = None
        try:
            tcp = LocalDNS(tcp_mode, tcp=True, port=0 if attempt == 0 else 49152 + secrets.randbelow(16384))
            udp = LocalDNS(udp_mode, port=tcp.port)
            break
        except OSError as exc:
            for peer in (udp, tcp):
                if peer is not None:
                    # No serve_forever thread has started: shutdown would wait
                    # for a thread that does not exist. Close the held socket.
                    peer.server.server_close()
            if (exc.errno not in {errno.EADDRINUSE, errno.EACCES}
                    and getattr(exc, "winerror", None) not in {10048, 10013}) or attempt == 31:
                raise
    with ExitStack() as stack:
        # Also close a bound peer if its thread could not be started.
        stack.callback(tcp.server.server_close)
        stack.callback(udp.server.server_close)
        stack.enter_context(tcp)
        stack.enter_context(udp)
        yield udp, tcp


class PairedDNSFixtureTests(unittest.TestCase):
    def test_collision_closes_held_socket_and_retries_before_starting_servers(self):
        first, tcp, udp = MagicMock(), MagicMock(), MagicMock()
        first.port, tcp.port = 51001, 51002
        for peer in (tcp, udp):
            peer.__enter__.return_value = peer
        with patch(__name__ + ".LocalDNS", side_effect=[first, OSError(errno.EADDRINUSE, "synthetic bind collision"), tcp, udp]) as factory:
            with paired_dns() as peers:
                self.assertEqual(peers, (udp, tcp))
                first.server.server_close.assert_called_once_with()
                first.__enter__.assert_not_called()
                self.assertEqual(factory.call_args_list[1].kwargs["port"], first.port)
                self.assertEqual(factory.call_args_list[3].kwargs["port"], tcp.port)
                self.assertEqual(factory.call_args_list[0].kwargs["port"], 0)
                self.assertIn(factory.call_args_list[2].kwargs["port"], range(49152, 65536))
        tcp.server.server_close.assert_called_once_with()
        udp.server.server_close.assert_called_once_with()

    def test_unrelated_bind_errors_are_not_retried(self):
        with patch(__name__ + ".LocalDNS", side_effect=OSError(errno.EIO, "synthetic unrelated error")) as factory:
            with self.assertRaises(OSError):
                with paired_dns():
                    self.fail("Unexpected fixture entry")
        self.assertEqual(factory.call_count, 1)

    def test_allocation_retries_have_a_hard_limit(self):
        with patch(__name__ + ".LocalDNS", side_effect=OSError(errno.EACCES, "synthetic reserved port")) as factory:
            with self.assertRaises(OSError):
                with paired_dns():
                    self.fail("Unexpected fixture entry")
        self.assertEqual(factory.call_count, 32)


def settings(kind="dns", **kwargs):
    data = {"check_type": kind, "target": "check.invalid" if kind == "dns" else "127.0.0.1", "count": 1, **kwargs}
    return prepare_check(data)[1]


class CheckSettingsTests(unittest.TestCase):
    def test_prepare_is_pure_and_transport_family_is_not_qtype(self):
        with patch("socket.getaddrinfo", side_effect=AssertionError("No warmup")), patch("subprocess.Popen", side_effect=AssertionError("No probe")):
            value = settings(record_type="AAAA", resolver_mode="custom", resolver_address="127.0.0.1")
            self.assertEqual(value["record_type"], "AAAA")
            self.assertEqual(value["target"], "check.invalid.")
            self.assertEqual(settings(record_type="PTR", target="2001:db8::1")["target"], "2001:db8::1")
            self.assertEqual(settings("loss", target="absolute.invalid.")["target"], "absolute.invalid.")
            self.assertEqual(validate_check_settings("dns", value), value)

    def test_invalid_options_counts_and_targets_rejected(self):
        for changes in ({"count": True}, {"count": 6}, {"timeout_ms": 100000}, {"record_type": "ANY"}, {"record_type": "AXFR"},
                        {"target": "--help"}, {"target": "https://example.invalid"}, {"target": "a\nb"}, {"target": "a" * 64},
                        {"resolver_mode": "custom", "resolver_address": "resolver.invalid"}, {"resolver_port": False}, {"command": ["evil"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settings(**changes)
        with self.assertRaises(ValueError):
            settings("loss", address_family="4", count=61)

    def test_capabilities_are_serializable_and_make_no_network_query(self):
        with patch("socket.socket", side_effect=AssertionError("No network capability test")), patch("app.checks.configured_resolvers", return_value=["127.0.0.1"]):
            value = check_capabilities()
        json.dumps(value)
        self.assertTrue(value["dns"]["configured_resolver"])
        self.assertIn("untested", value["loss"]["note"])


class DNSCheckTests(unittest.TestCase):
    def capture(self, port, transport="udp", **kwargs):
        value = settings(resolver_mode="custom", resolver_address="127.0.0.1", resolver_port=port, transport=transport, **kwargs)
        capture = CheckCapture("dns", value)
        snapshots = []
        with patch("app.checks.capture_time_quality", return_value=time_quality_result()):
            result = capture.run(snapshots.append)
        validate_check_result("dns", result, value)
        self.assertTrue(all(not child.poll() is None for child in capture.processes))
        return value, result, snapshots

    def test_actual_owned_udp_dns_child_and_ttl_resolver_clock_checkpoints(self):
        with LocalDNS() as peer:
            value, result, snapshots = self.capture(peer.port)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(check_run_status(result), "completed")
        self.assertEqual(len(peer.queries), 1)
        attempt = result["attempts"][0]
        self.assertEqual(attempt["answers"][0], {"name": "check.invalid.", "type": "A", "class": "IN", "ttl": 37, "rdata": "203.0.113.7"})
        self.assertEqual(attempt["resolver_address"], "127.0.0.1")
        self.assertEqual(attempt["resolver_port"], peer.port)
        self.assertEqual(attempt["resolver_family"], 4)
        self.assertEqual(snapshots[0]["status"], "running")
        self.assertEqual(snapshots[0]["time_quality"]["status"], "pending")
        self.assertEqual(snapshots[-1]["status"], "captured")
        self.assertEqual(snapshots[0]["attempts"], [])

    def test_actual_tcp_query_is_separate_from_qtype(self):
        with LocalDNS(tcp=True) as peer:
            _, result, _ = self.capture(peer.port, transport="tcp")
        self.assertEqual(result["attempts"][0]["transport"], "tcp")
        self.assertEqual(peer.transports, ["tcp"])

    def test_owned_worker_is_independent_of_launcher_working_directory(self):
        previous = os.getcwd()
        try:
            os.chdir(Path(__file__).resolve().parent)
            with LocalDNS() as peer:
                _, result, _ = self.capture(peer.port)
        finally:
            os.chdir(previous)
        self.assertEqual(result["status"], "captured")

    def test_private_unicode_metadata_preserves_character_limits_without_child_payload(self):
        with LocalDNS() as peer:
            value, result, _ = self.capture(peer.port, label="😀" * 200, notes="😀" * 4000,
                                            tags={key: "😀" * 100 for key in ("site", "device", "circuit", "ticket")})
        self.assertEqual(len(value["label"]), 200)
        self.assertEqual(len(value["notes"]), 4000)
        self.assertEqual(result["status"], "captured")

    def test_negative_nodata_server_error_are_completed_observations(self):
        for mode, outcome, rcode in (("nxdomain", "nxdomain", "NXDOMAIN"), ("nodata", "nodata", "NOERROR"), ("servfail", "dns_error", "SERVFAIL")):
            with self.subTest(mode=mode), LocalDNS(mode) as peer:
                _, result, _ = self.capture(peer.port)
            self.assertEqual(result["status"], "captured")
            self.assertEqual(result["attempts"][0]["status"], outcome)
            self.assertEqual(result["attempts"][0]["rcode"], rcode)

    def test_timeout_distinct_from_negative_response_and_bounded(self):
        start = time.monotonic()
        with LocalDNS("timeout") as peer:
            _, result, _ = self.capture(peer.port, timeout_ms=250)
        self.assertLess(time.monotonic() - start, 4)
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["attempts"][0]["rcode"])
        self.assertEqual(result["summary"]["responses"], 0)

    def test_truncated_udp_failed_tcp_preserves_both_and_partial(self):
        with paired_dns(tcp_mode="timeout") as (udp, tcp):
            _, result, _ = self.capture(udp.port, timeout_ms=500)
        self.assertEqual([row["transport"] for row in result["attempts"]], ["udp", "tcp"])
        self.assertEqual(result["attempts"][0]["status"], "truncated")
        self.assertEqual(result["attempts"][1]["status"], "error")
        self.assertTrue(result["attempts"][1]["error"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["summary"]["attempted_queries"], 1)

    def test_truncated_udp_successful_tcp_records_both_actual_transports(self):
        with paired_dns() as (udp, tcp):
            _, result, _ = self.capture(udp.port)
        self.assertEqual([row["transport"] for row in result["attempts"]], ["udp", "tcp"])
        self.assertEqual([row["status"] for row in result["attempts"]], ["truncated", "answer"])
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["summary"]["responses"], 2)
        self.assertEqual(result["summary"]["outcomes"]["answer"], 1)

    def test_tcp_fallback_uses_remaining_logical_query_budget(self):
        value = settings(resolver_mode="custom", resolver_address="127.0.0.1", timeout_ms=250)
        resolver = {"mode": "custom", "configured_addresses": ["127.0.0.1"], "selected_address": "127.0.0.1", "port": 53, "address_family": 4}
        def udp(query, *args, **kwargs):
            time.sleep(.08)
            reply = dns.message.make_response(query)
            reply.flags |= dns.flags.TC
            return reply
        def tcp(query, *args, **kwargs):
            self.assertLess(kwargs["timeout"], .2)
            self.assertGreater(kwargs["timeout"], 0)
            return dns.message.make_response(query)
        with patch("dns.query.udp", side_effect=udp), patch("dns.query.tcp", side_effect=tcp):
            result = query_dns(value, resolver, 1)
        self.assertEqual(len(result["attempts"]), 2)

    def test_resource_records_are_bounded_with_explicit_incomplete_evidence(self):
        value = settings(resolver_mode="custom", resolver_address="127.0.0.1")
        resolver = {"mode": "custom", "configured_addresses": ["127.0.0.1"], "selected_address": "127.0.0.1", "port": 53, "address_family": 4}
        def reply(query, *args, **kwargs):
            response = dns.message.make_response(query)
            response.answer.append(dns.rrset.from_text(query.question[0].name, 37, "IN", "A", *[f"203.0.113.{n}" for n in range(1, 150)]))
            return response
        with patch("dns.query.udp", side_effect=reply):
            rows = query_dns(value, resolver, 1)["attempts"]
        self.assertEqual(len(rows[0]["answers"]), 128)
        self.assertTrue(rows[0]["records_truncated"])

    def test_no_configured_resolver_and_missing_dependency_never_fallback(self):
        for name, replacement in (("configured_resolvers", []), ("_version", None)):
            with self.subTest(name=name), patch("app.checks." + name, return_value=replacement), patch("app.checks.capture_time_quality", return_value=time_quality_result()), patch("app.checks.CheckCapture._worker", side_effect=AssertionError("No public fallback")):
                result = CheckCapture("dns", settings()).run()
                self.assertEqual(result["status"], "unavailable")
                self.assertEqual(result["attempts"], [])

    def test_native_reply_association_checks_wrong_dns_id(self):
        for mode in ("wrong_id", "wrong_question", "wrong_source"):
            with self.subTest(mode=mode), LocalDNS(mode) as peer:
                _, result, _ = self.capture(peer.port)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["summary"]["responses"], 0)
            self.assertIsNone(result["attempts"][0]["rcode"])

    def test_udp_completed_checkpoint_survives_cancellation_during_fallback(self):
        with paired_dns(tcp_mode="stall") as (udp, tcp):
            value = settings(resolver_mode="custom", resolver_address="127.0.0.1", resolver_port=udp.port, timeout_ms=3000)
            capture, saved = CheckCapture("dns", value), []
            def checkpoint(result):
                saved.append(result)
                if result["attempts"]:
                    capture.cancel()
            with patch("app.checks.capture_time_quality", return_value=time_quality_result()):
                result = capture.run(checkpoint)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["attempts"][0]["status"], "truncated")
        self.assertTrue(any(row["status"] == "running" and row["attempts"] for row in saved))
        self.assertFalse(capture.processes)

    def test_archive_failure_after_udp_checkpoint_propagates_and_reaps(self):
        with paired_dns(tcp_mode="stall") as (udp, tcp):
            value = settings(resolver_mode="custom", resolver_address="127.0.0.1", resolver_port=udp.port, timeout_ms=3000)
            capture = CheckCapture("dns", value)
            def checkpoint(result):
                if result["attempts"]:
                    raise OSError("archive full after first response")
            with patch("app.checks.capture_time_quality", return_value=time_quality_result()), self.assertRaises(OSError):
                capture.run(checkpoint)
        self.assertFalse(capture.processes)
        self.assertTrue(capture.stop.is_set())


class LossCheckTests(unittest.TestCase):
    def probe(self, output, code=0, status="ok"):
        value = settings("loss")
        capture = CheckCapture("loss", value)
        destination = {"selected_address": "127.0.0.1", "address_family": 4}
        with patch("app.checks.shutil.which", return_value="ping"), patch.object(capture, "_process", return_value={"status": status, "raw": output, "exit_code": code, "truncated": False}):
            return capture._ping(1, destination, time.monotonic() + 5)

    def test_windows_unreachable_exit_zero_is_not_echo_reply(self):
        row = self.probe("Reply from 127.0.0.1: Destination host unreachable.\n    Packets: Sent = 1, Received = 1, Lost = 0 (0% loss),\n")
        self.assertEqual(row["status"], "unreachable")
        self.assertEqual((row["transmitted"], row["echo_replies"]), (1, 0))

    def test_numeric_and_upper_bound_rtt_and_unknown_locales(self):
        row = self.probe("Reply from 127.0.0.1: bytes=32 time<1ms TTL=128")
        self.assertEqual(row["status"], "reply")
        self.assertIsNone(row["rtt_ms"])
        self.assertEqual(row["rtt_upper_bound_ms"], 1)
        unknown = self.probe("Unsupported localized native output", code=0)
        self.assertEqual(unknown["status"], "malformed")
        self.assertIsNone(unknown["transmitted"])
        self.assertIsNone(unknown["echo_replies"])

    def test_loss_uses_observed_transmission_not_configured_probe_count(self):
        value = settings("loss", count=4)
        result = initial_result("loss", value)
        result["attempts"] = [self.probe("1 packets transmitted, 0 received, 100% packet loss")]
        summary = summarize_check("loss", result, value)
        self.assertEqual(summary["unattempted"], 3)
        self.assertEqual(summary["transmitted"], 1)
        self.assertEqual(summary["nonresponse_percent"], 100)
        result["attempts"].append(self.probe("", status="cancelled"))
        summary = summarize_check("loss", result, value)
        self.assertIsNone(summary["transmitted"])
        self.assertIsNone(summary["nonresponse_percent"])
        self.assertEqual(summary["confirmed_nonresponses"], 1)

    def test_permission_failure_is_not_counted_as_loss(self):
        row = self.probe("ping: socket: Operation not permitted", code=2)
        self.assertEqual(row["status"], "permission_blocked")
        self.assertIsNone(row["transmitted"])
        self.assertIsNone(row["echo_replies"])

    def test_resolution_once_then_all_probes_use_pinned_address(self):
        value = settings("loss", target="local.invalid", count=2)
        capture = CheckCapture("loss", value)
        with patch("app.checks.capture_time_quality", return_value=time_quality_result()), patch.object(capture, "_worker", return_value={"status": "captured", "candidates": [{"address": "127.0.0.1", "address_family": 4}]} ) as worker, patch.object(capture, "_process", return_value={"status": "ok", "raw": "time=1.2 ms", "exit_code": 0, "truncated": False}) as command:
            result = capture.run()
        self.assertEqual(result["status"], "captured")
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(command.call_count, 2)
        self.assertTrue(all(call.args[0][-1] == "127.0.0.1" for call in command.call_args_list))
        self.assertEqual(result["resolution"]["selected_address"], "127.0.0.1")

    @unittest.skipUnless(shutil.which("ping.exe" if sys.platform == "win32" else "ping"), "Native ping absent")
    def test_actual_native_loopback_echo_or_honest_permission_evidence(self):
        value = settings("loss")
        with patch("app.checks.capture_time_quality", return_value=time_quality_result()):
            result = CheckCapture("loss", value).run()
        validate_check_result("loss", result, value)
        self.assertEqual(result["resolution"]["selected_address"], "127.0.0.1")
        self.assertIn(result["attempts"][0]["status"], {"reply", "permission_blocked", "unavailable"})
        if result["attempts"][0]["status"] == "reply":
            self.assertEqual(result["summary"]["nonresponse_percent"], 0)
        else:
            self.assertIsNone(result["summary"]["nonresponse_percent"])


class CheckFailureTests(unittest.TestCase):
    def test_cancel_kills_owned_worker_without_waiting_for_dns_deadline(self):
        with LocalDNS("timeout") as peer:
            value = settings(resolver_mode="custom", resolver_address="127.0.0.1", resolver_port=peer.port, timeout_ms=3000)
            capture, results = CheckCapture("dns", value), []
            with patch("app.checks.capture_time_quality", return_value=time_quality_result()):
                thread = threading.Thread(target=lambda: results.append(capture.run()))
                thread.start()
                deadline = time.monotonic() + 3
                while not peer.queries and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(peer.queries)
                start = time.monotonic()
                capture.cancel()
                thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertLess(time.monotonic() - start, 2)
            self.assertEqual(results[0]["status"], "cancelled")
            self.assertFalse(capture.processes)

    def test_storage_failure_propagates_stops_before_any_query(self):
        capture = CheckCapture("dns", settings())
        with patch.object(capture, "_worker", side_effect=AssertionError("Must not query")), self.assertRaises(OSError):
            capture.run(lambda value: (_ for _ in ()).throw(OSError("archive full")))
        self.assertTrue(capture.stop.is_set())
        self.assertFalse(capture.processes)

    def test_owned_process_output_is_bounded_and_reaped(self):
        capture = CheckCapture("loss", settings("loss"))
        observed = capture._process([sys.executable, "-c", "import sys;sys.stdout.write('x'*100000);sys.stdout.flush()"], 2, limit=8192)
        self.assertLessEqual(len(observed["raw"].encode()), 8192)
        self.assertTrue(observed["truncated"])
        self.assertFalse(capture.processes)

    def test_bad_import_status_numbers_and_summary_raise_valueerror(self):
        value = settings("loss")
        original = initial_result("loss", value)
        for field, bad in (("status", []), ("status", {}), ("elapsed_seconds", 10**1000), ("elapsed_seconds", float("inf")), ("elapsed_seconds", True), ("schema_version", True), ("summary", {"attempted": 1})):
            poisoned = copy.deepcopy(original)
            poisoned[field] = bad
            with self.subTest(field=field, bad=repr(bad)[:30]), self.assertRaises(ValueError):
                validate_check_result("loss", poisoned, value)
        record = {"kind": "diagnostic-check", "check_type": "loss", "settings": value, "result": original, "status": "completed"}
        with self.assertRaises(ValueError):
            validate_check_record(record)
        poisoned = copy.deepcopy(original)
        poisoned["summary"]["requested"] = True
        with self.assertRaises(ValueError):
            validate_check_result("loss", poisoned, value)

    def test_owned_process_hard_deadline_and_external_cancellation(self):
        capture = CheckCapture("loss", settings("loss"))
        start = time.monotonic()
        observed = capture._process([sys.executable, "-c", "import time;time.sleep(30)"], .5)
        self.assertEqual(observed["status"], "timeout")
        self.assertLess(time.monotonic() - start, 1.5)
        self.assertFalse(capture.processes)


if __name__ == "__main__":
    unittest.main()
