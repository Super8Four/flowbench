"""Generate disposable, reserved-address report fixtures for visual CI QA.

Usage: python scripts/check-reports.py --output-dir /qa
Then run the Documents skill render_docx.py on each DOCX, and render the
ReportLab PDFs with pdftoppm. Generated files never belong in release assets.
"""
import argparse
import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile

source_root = Path(__file__).resolve().parents[1]
if not (source_root / "app").is_dir():
    source_root = Path("/opt/iperf-workspace")
sys.path.insert(0, str(source_root))

from app.diagnostics import latency_summary
from app.flent import normalize as normalize_flent, RRUL_TCP, RRUL_RTT
from app.geolocation import empty_location, geolocation_metadata
from app.projects import ProjectStore
from app.reports import prepare_report, render_pdf, render_docx
from app.runner import RunManager, settings_from
from app.time_quality import time_quality_result
from app.checks import prepare_check, initial_result, summarize_check, check_run_status, validate_check_record
from app.endpoints import derive_endpoints, observe_iperf_start


def check_fixture(check_type, run_id="e" * 32, partial=False):
    """Synthetic typed observations only; never call a resolver or native tool."""
    _, settings = prepare_check({"check_type": check_type, "settings": {
        "target": "report-fixture.example" if check_type == "dns" else "192.0.2.25",
        "count": 3 if check_type == "dns" else 4,
        "label": "Synthetic " + check_type + " observations", "notes": "Disposable report fixture"}})
    result = initial_result(check_type, settings)
    result.update(status="partial" if partial else "captured", started_at="2026-01-01T10:00:00+00:00",
                  finished_at="2026-01-01T10:00:04+00:00", elapsed_seconds=4,
                  time_quality=time_quality_result("unavailable", "Synthetic fixture; local clock provider not queried."))
    result["time_quality"]["observed_at"] = "2026-01-01T10:00:00+00:00"
    def timing(sequence):
        return {"sequence": sequence, "started_at": "2026-01-01T10:00:00+00:00", "finished_at": "2026-01-01T10:00:00.002500+00:00", "duration_ms": 2.5}
    if check_type == "dns":
        result["resolver"] = {"mode": "system", "configured_addresses": ["192.0.2.53"], "selected_address": "192.0.2.53", "port": 53, "address_family": 4}
        plan = [(1, "udp", "truncated", "NOERROR"), (1, "tcp", "answer", "NOERROR"), (2, "udp", "nodata", "NOERROR"), (3, "udp", "nxdomain", "NXDOMAIN")]
        if partial:
            plan = [(1, "udp", "timeout", None)]
        for sequence, (query, transport, status, rcode) in enumerate(plan, 1):
            row = dict(timing(sequence), query_index=query, transport=transport, resolver_address="192.0.2.53", resolver_port=53, resolver_family=4,
                       status=status, rcode=rcode, flags=["QR", "TC"] if status == "truncated" else ["QR", "RD", "RA"] if rcode else [],
                       question={"name": settings["target"], "type": "A", "class": "IN"}, answers=[], authority=[], additional=[],
                       records_truncated=False, raw_output="Synthetic resolver report-fixture.example 192.0.2.53", output_truncated=False,
                       error="Synthetic query timed out" if status == "timeout" else None)
            if status == "answer":
                row["answers"] = [{"name": settings["target"], "type": "A", "class": "IN", "ttl": 60, "rdata": "192.0.2.25"},
                                  {"name": settings["target"], "type": "TXT", "class": "IN", "ttl": 0, "rdata": '"Synthetic <literal>& record"'}]
            if status in {"nodata", "nxdomain"}:
                row["authority"] = [{"name": "example.", "type": "SOA", "class": "IN", "ttl": 120, "rdata": "ns.example. hostmaster.example. 1 60 60 60 60"}]
            result["attempts"].append(row)
    else:
        result["resolution"] = {"status": "captured", "started_at": "2026-01-01T10:00:00+00:00", "finished_at": "2026-01-01T10:00:00.001000+00:00", "duration_ms": 1,
                                "source": "numeric", "candidates": [{"address": settings["target"], "address_family": 4}], "selected_address": settings["target"], "address_family": 4, "error": None}
        for sequence, (status, rtt, bound) in enumerate((("reply", 2.5, None), ("reply", None, 1), ("no_reply", None, None), ("unreachable", None, None)), 1):
            row = dict(timing(sequence), address=settings["target"], address_family=4, status=status, rtt_ms=rtt, rtt_upper_bound_ms=bound,
                       transmitted=1, echo_replies=1 if status == "reply" else 0, exit_code=0 if status == "reply" else 1,
                       raw_output="Synthetic ICMP 192.0.2.25", output_truncated=False, error=None)
            if partial and sequence == 4:
                row.update(status="permission_blocked", transmitted=None, echo_replies=None, error="Synthetic permission unavailable")
            result["attempts"].append(row)
    result["summary"] = summarize_check(check_type, result, settings)
    record = {"schema_version": 1, "kind": "diagnostic-check", "check_type": check_type, "id": run_id,
              "created_at": result["started_at"], "finished_at": result["finished_at"], "status": check_run_status(result),
              "settings": settings, "result": result, "annotations": [], "summary": {}, "intervals": [], "command": []}
    validate_check_record(record)
    return record


def fixture():
    baseline = [{"status": "reply", "rtt_ms": value, "rtt_upper_bound_ms": None} for value in (2.0, 2.1, 2.2)]
    loaded = [{"status": "reply", "rtt_ms": value, "rtt_upper_bound_ms": None} for value in (4.0, 4.1, 4.2, 4.3)] + [{"status": "no_reply", "rtt_ms": None}]
    quality = time_quality_result("captured", "Synthetic local provider observation; no external time query was performed.")
    quality.update(observed_at="2026-01-01T09:59:58.200000+00:00", provider="chrony", source="192.0.2.123",
                   synchronized=True, offset_seconds=-.0000004, uncertainty_seconds=.0000012,
                   uncertainty_kind="root_dispersion_proxy", root_delay_seconds=.002, root_dispersion_seconds=.0000012,
                   last_sync_at="2026-01-01T09:59:50+00:00", query_elapsed_seconds=.05,
                   command=["/usr/bin/chronyc", "-n", "-c", "-h", "127.0.0.1", "tracking"])
    geo = geolocation_metadata("available", "Synthetic location fixture only; no real IP lookup, dataset download or network traffic.")
    geo.update(database_type="DBIP-City-Lite", build_epoch=1767225600)
    located = []
    for address, city, latitude, longitude in (("8.8.8.8", "Synthetic city A", 12.5, -34.75),
                                               ("2001:4860:4860::8888", "Synthetic city B", -25.25, 130.5)):
        location = empty_location(address)
        location.update(status="located", city=city, country="Synthetic country", country_code="ZZ", latitude=latitude, longitude=longitude)
        located.append(location)
    return {"schema_version": 1, "id": "a" * 32, "created_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:00:10+00:00", "status": "completed",
            "settings": settings_from({"target": "192.0.2.10", "label": "Synthetic before change", "notes": "Disposable report layout fixture"}),
            "iperf_version": "iperf 3.22", "output_format": "json", "exit_code": 0, "command": [], "error": None,
            "summary": {"sum_received": {"bits_per_second": 100e6}, "sum_sent": {"retransmits": 2}},
            "intervals": [{"sum": {"start": i, "end": i + 1, "bits_per_second": rate * 1e6}} for i, rate in ((0, 90), (1, 110), (4, 120), (5, 100))], "test_parameters": None,
            "annotations": [{"id": "d" * 32, "timestamp": "2026-01-01T10:00:04+00:00", "seconds": 4, "text": "Synthetic note at the first captured interval after a gap"}],
            "preflight": {"schema_version": 1, "policy": "once_per_test", "status": "captured", "phase": "before_traffic",
                          "started_at": "2026-01-01T09:59:58+00:00", "finished_at": "2026-01-01T09:59:59+00:00",
                          "elapsed_seconds": 1, "time_quality": quality},
            "route": {"status": "captured", "phase": "before_traffic", "started_at": "2026-01-01T09:59:58+00:00",
                      "finished_at": "2026-01-01T09:59:58.650000+00:00", "elapsed_seconds": .65,
                      "geolocation": geo,
                      "hops": [{"hop": 1, "address": "192.168.1.1", "addresses": ["192.168.1.1"],
                                "rtt_ms": [1.2, 1.3], "rtt_upper_bound_ms": [1], "responded": True, "observed": True,
                                "locations": [empty_location("192.168.1.1")]},
                               {"hop": 2, "address": "8.8.8.8", "addresses": ["8.8.8.8", "2001:4860:4860::8888"],
                                "rtt_ms": [2.5], "rtt_upper_bound_ms": [3], "responded": True, "observed": True, "locations": located,
                                "observations": [{"addresses": ["8.8.8.8"], "rtt_ms": [2.5], "rtt_upper_bound_ms": [], "detail": "Synthetic reply A"},
                                                 {"addresses": ["2001:4860:4860::8888"], "rtt_ms": [], "rtt_upper_bound_ms": [3], "detail": "Synthetic reply B"}]},
                               {"hop": 3, "address": None, "addresses": [], "rtt_ms": [], "rtt_upper_bound_ms": [],
                                "responded": False, "observed": False, "locations": [empty_location(None)]}], "raw_output": "Reserved fixture"},
            "diagnostics": {"schema_version": 1, "latency": {"enabled": True, "metric": "ICMP echo RTT",
                            "baseline": {"status": "captured", "samples": baseline, "summary": latency_summary(baseline)},
                            "loaded": {"status": "captured", "samples": loaded, "summary": latency_summary(loaded)}, "mean_increase_ms": 2.05},
                            "endpoint_health": {"enabled": True, "local": {"provenance": "detected local", "delta": {"cpu_busy_percent": 15.2}}, "remote": {"status": "unavailable"}},
                            "path_context": {"mtu": {"status": "disabled", "tested_packet_sizes": [], "largest_confirmed_packet_bytes": None}}}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"fixture_only": True, "source": "Synthetic reserved-address data; no network probes", "files": []}
    with tempfile.TemporaryDirectory() as archive:
        manager = RunManager(archive, "missing-iperf")
        try:
            before = fixture()
            # Synthetic native socket observations, distinct from the synthetic
            # HTTP controller. No host identity lookup or network work occurs.
            before["endpoints"] = observe_iperf_start(before, {
                "test_start": {"protocol": "TCP"}, "connecting_to": {"host": "192.0.2.10"},
                "connected": [{"socket": 7, "local_host": "192.0.2.11", "local_port": 43117,
                               "remote_host": "192.0.2.10", "remote_port": 5201},
                              {"socket": 8, "local_host": "fe80::11%fixture_scope", "local_port": 43118,
                               "remote_host": "2001:db8::10", "remote_port": 5201}]})
            before["endpoints"]["execution_hostname"] = "synthetic-container.example"
            before["initiator_context"] = {"schema_version": 1, "observed_at": "2026-01-01T10:00:00Z",
                                           "source": "wsgi_remote_addr", "peer": {"address": "203.0.113.19", "port": None, "family": 4}}
            project = ProjectStore(manager).create({"name": "Synthetic circuit change", "description": "Disposable comparison fixture"})
            before["settings"]["project_id"] = project["id"]
            after = copy.deepcopy(before)
            after["id"] = "b" * 32
            after["settings"].update(label="Synthetic after change", streams=2)
            after["summary"]["sum_received"]["bits_per_second"] = 180e6
            after["intervals"] = [{"sum": {"start": i, "end": i + 1, "bits_per_second": rate * 1e6}} for i, rate in ((0, 160), (1, 190), (4, 200), (5, 170))]
            manager.save(before)
            manager.save(after)
            comparison_ids = [before["id"], after["id"]]
            before["settings"].update(duration=30, color="#183b50")
            before["finished_at"] = "2026-01-01T10:00:30+00:00"
            before["intervals"] = [{"sum": {"start": i, "end": i + 1, "bits_per_second": rate * 1e6}} for i, rate in ((0, 90), (1, 110), (20, 120), (29, 100))]
            manager.save(before)
            for ordinal, rate, color in ((5, 0, "#b52b3d"), (6, .0005, "#437456"), (7, 3000, "#884f00")):
                extra = copy.deepcopy(after)
                extra["id"] = format(ordinal, "032x")
                extra["settings"].update(label="Synthetic selected test " + str(ordinal), duration=10, color=color)
                extra["summary"]["sum_received"]["bits_per_second"] = rate * 1e6
                extra["intervals"] = [{"sum": {"start": i, "end": i + 1, "bits_per_second": rate * 1e6}} for i in (0, 1, 4, 9)]
                manager.save(extra)
                comparison_ids.append(extra["id"])
            flent = copy.deepcopy(before)
            flent.update(id="c" * 32, kind="flent-run", summary={}, intervals=[], iperf_version=None,
                         diagnostics=None, annotations=[])
            flent["settings"].update(engine="flent", flent_workload="rrul", streams=4, mode="bidir", port=12865,
                                     bandwidth="0", label="Synthetic complete native Flent format")
            native = {"version": 4, "metadata": {"NAME": "rrul", "HOST": "192.0.2.10", "LENGTH": 30},
                      "x_values": [0, 1, 2, 3],
                      "results": {name: [index + 1, index + 2, None, index + 4]
                                  for index, name in enumerate(sorted(RRUL_TCP | RRUL_RTT))}}
            flent["flent"] = normalize_flent(native, "Synthetic fixture")
            flent.pop("endpoints", None)
            flent["endpoints"] = derive_endpoints(flent)
            flent["endpoints"]["execution_hostname"] = "synthetic-container.example"
            manager.save(flent)
            for check_type, run_id in (("dns", "e" * 32), ("loss", "f" * 32)):
                captured = check_fixture(check_type, run_id)
                captured["endpoints"] = derive_endpoints(captured)
                captured["endpoints"]["execution_hostname"] = "synthetic-container.example"
                captured["initiator_context"] = copy.deepcopy(before["initiator_context"])
                manager.save(captured)
                rerun = check_fixture(check_type, ("8" if check_type == "dns" else "9") * 32, partial=True)
                rerun["endpoints"] = derive_endpoints(rerun)
                rerun["endpoints"]["execution_hostname"] = "synthetic-container.example"
                rerun["initiator_context"] = copy.deepcopy(before["initiator_context"])
                rerun["lineage"] = {"schema_version": 1, "action": "run_again", "source_run_id": run_id, "external_source_id": None}
                manager.save(rerun)
            for name, ids, redact in (("single", [before["id"]], False), ("comparison-redacted", comparison_ids, True), ("flent", [flent["id"]], False),
                                      ("dns-check", ["e" * 32, "8" * 32], False), ("loss-check", ["f" * 32, "9" * 32], True)):
                model = prepare_report(manager, {"ids": ids, "redact": redact})
                (args.output_dir / (name + ".json")).write_text(json.dumps(model, indent=2), encoding="utf-8")
                for extension, renderer in (("pdf", render_pdf), ("docx", render_docx)):
                    payload = renderer(model)
                    path = args.output_dir / (name + "." + extension)
                    path.write_bytes(payload)
                    if extension == "pdf":
                        assert payload.startswith(b"%PDF-"), path
                    else:
                        with ZipFile(BytesIO(payload)) as package:
                            assert model["title"].encode() in package.read("word/document.xml"), path
                            assert package.testzip() is None, path
                    manifest["files"].append({"name": path.name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
        finally:
            manager.shutdown()
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Generated and validated ten PDF/DOCX fixtures. Visual rendering is a separate required check.")


if __name__ == "__main__":
    main()
