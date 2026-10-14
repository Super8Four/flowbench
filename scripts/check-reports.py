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
            manager.save(flent)
            for name, ids, redact in (("single", [before["id"]], False), ("comparison-redacted", comparison_ids, True), ("flent", [flent["id"]], False)):
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
                            assert b"Network performance report" in package.read("word/document.xml"), path
                            assert package.testzip() is None, path
                    manifest["files"].append({"name": path.name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
        finally:
            manager.shutdown()
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Generated and validated six PDF/DOCX fixtures. Visual rendering is a separate required check.")


if __name__ == "__main__":
    main()
