"""Bounded endpoint evidence: synthetic native sources and disposable archives only."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from app.endpoints import (derive_endpoints, endpoint_view, endpoints_summary,
                           initial_endpoints, observe_iperf_start, request_context,
                           runtime_hostname, validate_context, validate_endpoints)
from app.runner import RunManager
from test_annotations import sample_record


def start(socket_id=7, local="192.0.2.10", remote="198.51.100.20", local_port=41001, remote_port=5201):
    return {"connected": [{"socket": socket_id, "local_host": local, "local_port": local_port,
                            "remote_host": remote, "remote_port": remote_port}],
            "connecting_to": {"host": remote, "port": remote_port},
            "test_start": {"protocol": "TCP", "reverse": 1, "bidirectional": 1}}


class EndpointModelTests(unittest.TestCase):
    def test_pure_helpers_do_not_read_current_host_or_resolve(self):
        record = sample_record(native_result={"start": start()})
        before = copy.deepcopy(record)
        with patch("socket.gethostname", side_effect=AssertionError("current host accessed")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("resolution attempted")):
            self.assertIsNone(initial_endpoints(record)["execution_hostname"])
            value = derive_endpoints(record)
            self.assertIs(validate_endpoints(value, record), value)
            self.assertEqual(endpoint_view(record)["provenance"], "preserved_native")
            self.assertIsNone(endpoint_view(record)["execution_hostname"])
            self.assertIs(validate_context(request_context("::1"))["peer"]["port"], None)
        self.assertEqual(record, before)
        with patch("socket.gethostname", return_value="container-test"), \
                patch("socket.getaddrinfo", side_effect=AssertionError("resolution attempted")):
            self.assertEqual(runtime_hostname(), "container-test")

    def test_native_pairs_keep_reporter_perspective_control_is_separate(self):
        local = start(local="2001:db8::1", remote="fe80::2%eth0")
        peer = start(local="fe80::2%eth0", remote="2001:db8::1", local_port=5201, remote_port=41001)
        peer.pop("connecting_to")
        peer["accepted_connection"] = {"host": "2001:db8::1", "port": 41000}
        record = sample_record(native_result={"start": local, "server_output_json": json.dumps({"start": peer})})
        value = derive_endpoints(record)
        self.assertEqual(value["status"], "captured")
        self.assertEqual([row["reporter"] for row in value["sockets"]], ["local", "peer"])
        self.assertEqual([row["observer"] for row in value["sockets"]], ["client", "server"])
        self.assertEqual(value["sockets"][0]["local"], {"address": "2001:db8::1", "family": 6, "port": 41001})
        self.assertEqual(value["sockets"][1]["remote"]["port"], 41001)
        self.assertEqual(value["control_peers"][0]["peer"]["port"], 41000)
        self.assertEqual(value["sockets"][0]["remote"]["address"], "fe80::2%eth0")
        validate_endpoints(value, record)

    def test_stream_merge_survives_end_only_and_enriches_unknowns(self):
        record = sample_record(status="running", native_result=None)
        partial = start(local_port=None)
        partial.pop("test_start")
        record["endpoints"] = observe_iperf_start(record, partial)
        old = copy.deepcopy(record)
        self.assertEqual(record["endpoints"]["status"], "partial")
        record["native_result"] = {"start": start()}
        validate_endpoints(record["endpoints"], record)  # Unknown does not contradict known.
        record["endpoints"] = derive_endpoints(record)
        self.assertEqual(len(record["endpoints"]["sockets"]), 1)
        self.assertEqual(record["endpoints"]["sockets"][0]["protocol"], "tcp")
        self.assertEqual(record["endpoints"]["sockets"][0]["local"]["port"], 41001)
        self.assertIsNone(old["endpoints"]["sockets"][0]["local"]["port"])
        captured = copy.deepcopy(record["endpoints"])
        record["native_result"] = {"event": "end", "data": {}}
        self.assertEqual(derive_endpoints(record), captured)
        conflicting = copy.deepcopy(record)
        conflicting["native_result"] = {"start": start(local_port=49999)}
        with self.assertRaises(ValueError):
            validate_endpoints(captured, conflicting)

    def test_wildcard_native_is_unknown_but_raw_is_unchanged(self):
        for wildcard in ("0.0.0.0", "::"):
            with self.subTest(wildcard=wildcard):
                record = sample_record(native_result={"start": start(local=wildcard)})
                before = copy.deepcopy(record)
                value = derive_endpoints(record)
                self.assertEqual(value["status"], "partial")
                self.assertEqual(value["sockets"][0]["local"], {"address": None, "port": 41001, "family": None})
                self.assertEqual(record, before)
                validate_endpoints(value)
                value["sockets"][0]["local"].update(address=wildcard, family=4 if "." in wildcard else 6)
                with self.assertRaises(ValueError):
                    validate_endpoints(value)

    def test_malformed_native_and_surrogate_peer_never_mutate_archive(self):
        sources = [{"connected": {}}, {"connected": [None, {"local_host": [], "remote_port": True}]},
                   {"connected": [], "test_start": {"protocol": []}}]
        for source in sources:
            record = sample_record(native_result={"start": source, "server_output_json": "\ud800"})
            before = copy.deepcopy(record)
            view = endpoint_view(record)
            self.assertIn(view["status"], ("partial", "not_recorded"))
            self.assertEqual(record, before)
        record = sample_record(native_result={"server_output_json": "\ud800"})
        self.assertEqual(endpoint_view(record)["status"], "not_recorded")

    def test_capture_bounds_are_partial_and_preserve_full_source(self):
        source = start()
        source["connected"] = [dict(source["connected"][0], socket=n, local_port=20000 + n) for n in range(257)]
        record = sample_record(native_result={"start": source})
        record["endpoints"] = derive_endpoints(record)
        self.assertEqual(len(record["endpoints"]["sockets"]), 256)
        self.assertEqual(record["endpoints"]["status"], "partial")
        self.assertEqual(len(record["native_result"]["start"]["connected"]), 257)
        record["native_result"] = {"end": {}}
        self.assertEqual(derive_endpoints(record)["status"], "partial")

    def test_check_destinations_are_evidence_not_answers_or_source_guesses(self):
        dns = sample_record(kind="diagnostic-check", check_type="dns", result={"attempts": [
            {"resolver_address": "127.0.0.1", "resolver_port": 5353, "transport": "udp", "answers": ["203.0.113.7"]},
            {"resolver_address": "127.0.0.1", "resolver_port": 5353, "transport": "tcp"}]})
        loss = sample_record(kind="diagnostic-check", check_type="loss", result={"resolution": {
            "status": "captured", "selected_address": "2001:db8::3"}})
        flent = sample_record(kind="flent-run", flent={"data": {"metadata": {"HOST": "peer.invalid"}}})
        with patch("socket.getaddrinfo", side_effect=AssertionError("resolution attempted")):
            for record, purposes in ((dns, ["dns_resolver"] * 2), (loss, ["icmp_target"]), (flent, ["flent_reported_target"])):
                value = derive_endpoints(record)
                validate_endpoints(value)
                self.assertEqual(value["sockets"], [])
                self.assertEqual(value["control_peers"], [])
                self.assertEqual([row["purpose"] for row in value["destinations"]], purposes)
            self.assertEqual(derive_endpoints(dns)["destinations"][0]["endpoint"]["address"], "127.0.0.1")
            self.assertIsNone(derive_endpoints(loss)["destinations"][0]["endpoint"]["port"])
            self.assertIsNone(derive_endpoints(flent)["destinations"][0]["endpoint"]["address"])
            self.assertEqual(derive_endpoints(flent)["status"], "unavailable")

    def test_group_and_history_view_provenance_do_not_rewrite(self):
        for kind in ("protocol-job", "matrix-job"):
            record = sample_record(kind=kind, native_result={"start": start()})
            self.assertEqual(derive_endpoints(record)["status"], "not_applicable")
            self.assertEqual(derive_endpoints(record)["sockets"], [])
        record = sample_record()
        self.assertEqual(endpoint_view(record)["provenance"], "not_recorded")
        record["endpoints"] = initial_endpoints(record, "old-container")
        before = copy.deepcopy(record)
        self.assertEqual(endpoint_view(record)["status"], "unavailable")
        self.assertEqual(endpoint_view(record)["execution_hostname"], "old-container")
        self.assertEqual(endpoint_view(record)["provenance"], "captured_archive")
        self.assertEqual(record, before)
        record["imported"] = {"format": "archive"}
        self.assertEqual(endpoint_view(record)["provenance"], "imported_archive")
        record["endpoints"]["status"] = []
        poisoned = copy.deepcopy(record)
        self.assertEqual(endpoint_view(record)["status"], "unavailable")
        self.assertEqual(record, poisoned)
        with self.assertRaises(ValueError):
            validate_endpoints(record["endpoints"])

    def test_strict_metadata_types_and_source_coherence(self):
        record = sample_record(native_result={"start": start()})
        good = derive_endpoints(record)
        invalid = []
        for key, value in (("schema_version", True), ("status", []), ("status", "made_up"),
                           ("execution_hostname", "bad\nname"), ("execution_hostname", "\ud800"), ("sockets", [None])):
            invalid.append(dict(good, **{key: value}))
        for side, key, value in (("local", "family", True), ("local", "family", 6), ("local", "port", True),
                                 ("local", "port", 0), ("local", "port", 65536), ("remote", "address", "peer.invalid"),
                                 ("remote", "address", "fe80::1%bad scope")):
            candidate = copy.deepcopy(good)
            candidate["sockets"][0][side][key] = value
            invalid.append(candidate)
        candidate = copy.deepcopy(good)
        candidate["sockets"][0]["socket_id"] = True
        invalid.extend([candidate, dict(good, future="unknown"), dict(good, sockets=good["sockets"] * 257)])
        for value in invalid:
            with self.subTest(value=repr(value)[:120]), self.assertRaises(ValueError):
                validate_endpoints(value, record)
        self.assertIs(validate_endpoints(good, record), good)

    def test_context_is_explicit_utc_numeric_and_portless(self):
        good = request_context("fe80::1%12")
        self.assertIs(validate_context(good), good)
        self.assertEqual(good["peer"], {"address": "fe80::1%12", "port": None, "family": 6})
        for stamp in ("2026-10-14T12:00:00Z", "2026-10-14T12:00:00+00:00"):
            validate_context(dict(good, observed_at=stamp))
        invalid = [dict(good, schema_version=True), dict(good, source="x_forwarded_for"),
                   dict(good, observed_at="2026-10-14T12:00:00"), dict(good, observed_at="2026-10-14T12:00:00+01:00"),
                   dict(good, observed_at="2026-02-30T12:00:00Z"), dict(good, extra="untrusted")]
        candidate = copy.deepcopy(good)
        candidate["peer"]["port"] = 12345
        invalid.append(candidate)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_context(value)
        self.assertIsNone(request_context("not-a-numeric-peer")["peer"]["address"])


class EndpointArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.manager.capabilities.update(available=True, bidir=True, udp_bidir=True, json_stream=True)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_context_admission_replay_and_children_are_fresh_copies(self):
        context = request_context("192.0.2.31")
        body = {"target": "127.0.0.1", "protocol": "both", "request_id": "1" * 32}
        with patch.object(self.manager, "run_job"), patch("app.runner.runtime_hostname", return_value="capture-container"):
            record = self.manager.start(body, initiator_context=context)
            self.manager.worker.join(2)
        self.assertEqual(record["endpoints"]["status"], "not_applicable")
        self.assertEqual(record["endpoints"]["execution_hostname"], "capture-container")
        children = [self.manager.read(row["id"]) for row in record["children"]]
        self.assertEqual([child["initiator_context"] for child in children], [context, context])
        context["peer"]["address"] = "192.0.2.99"
        self.assertEqual(self.manager.read(record["id"])["initiator_context"]["peer"]["address"], "192.0.2.31")
        with patch("app.runner.runtime_hostname", side_effect=AssertionError("replay recaptured host")):
            replay = self.manager.start(body, initiator_context={"invalid": "ignored after replay"})
        self.assertEqual(replay["id"], record["id"])
        self.assertEqual(replay["initiator_context"]["peer"]["address"], "192.0.2.31")

    def test_forged_context_rejected_before_any_archive_or_request_claim(self):
        body = {"target": "127.0.0.1", "request_id": "2" * 32, "initiator_context": request_context("192.0.2.1")}
        with self.assertRaises(ValueError):
            self.manager.start(body)
        self.assertEqual(self.manager.storage.records(), [])
        self.assertIsNone(self.manager.storage.execution_request(body["request_id"]))
        with self.assertRaises(ValueError):
            self.manager.start({"target": "127.0.0.1"}, initiator_context={"source": "forged"})

    def test_streaming_checkpoint_persists_and_end_only_preserves_evidence(self):
        record = sample_record(status="running", native_result=None, finished_at=None)
        record["endpoints"] = initial_endpoints(record, "historical-container")
        self.manager.record, self.manager.active = record, record["id"]
        self.manager.save(record)
        self.manager.log("stdout", json.dumps({"event": "start", "data": start()}))
        saved = self.manager.storage.read(record["id"])
        self.assertEqual(saved["event_count"], 1)
        self.assertEqual(saved["endpoints"]["status"], "captured")
        self.assertEqual(len(self.manager.storage.events(record["id"])), 1)
        self.manager.log("stdout", json.dumps({"event": "end", "data": {"sum_received": {"bits_per_second": 1}}}))
        self.assertEqual(self.manager.storage.read(record["id"])["endpoints"], saved["endpoints"])
        export = self.manager.export(record["id"])
        self.assertNotIn("endpoint_view", export)
        history = self.manager.list_runs()[0]
        self.assertEqual(history["endpoints_summary"], {"status": "captured", "socket_count": 1, "destination_count": 0, "families": [4]})
        self.assertNotIn("endpoints", history)
        self.assertNotIn("initiator_context", history)
        self.manager.shutdown()
        with patch("socket.gethostname", side_effect=AssertionError("historical host lookup")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("historical resolution")):
            self.manager = RunManager(self.temp.name, "missing-iperf")
            reopened = self.manager.read(record["id"])
            self.assertEqual(reopened["endpoints"], saved["endpoints"])
            self.assertEqual(endpoint_view(reopened)["execution_hostname"], "historical-container")
            self.assertEqual(endpoints_summary(reopened)["socket_count"], 1)


if __name__ == "__main__":
    unittest.main()
