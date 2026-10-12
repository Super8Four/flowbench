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
from app.projects import ProjectStore
from app.reports import prepare_report, render_pdf, render_docx
from app.runner import RunManager, settings_from


def fixture():
    baseline = [{"status": "reply", "rtt_ms": value, "rtt_upper_bound_ms": None} for value in (2.0, 2.1, 2.2)]
    loaded = [{"status": "reply", "rtt_ms": value, "rtt_upper_bound_ms": None} for value in (4.0, 4.1, 4.2, 4.3)] + [{"status": "no_reply", "rtt_ms": None}]
    return {"schema_version": 1, "id": "a" * 32, "created_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:00:10+00:00", "status": "completed",
            "settings": settings_from({"target": "192.0.2.10", "label": "Synthetic before change", "notes": "Disposable report layout fixture"}),
            "iperf_version": "iperf 3.22", "output_format": "json", "exit_code": 0, "command": [], "error": None,
            "summary": {"sum_received": {"bits_per_second": 100e6}, "sum_sent": {"retransmits": 2}},
            "intervals": [{"sum": {"start": i, "end": i + 1, "bits_per_second": rate * 1e6}} for i, rate in ((0, 90), (1, 110), (4, 120), (5, 100))], "test_parameters": None,
            "annotations": [{"id": "d" * 32, "timestamp": "2026-01-01T10:00:04+00:00", "seconds": 4, "text": "Synthetic note at the first captured interval after a gap"}],
            "route": {"status": "captured", "hops": [{"hop": 1, "address": "192.0.2.1", "rtt_ms": [1.2, 1.3], "responded": True}], "raw_output": "Reserved fixture"},
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
            flent = copy.deepcopy(before)
            flent.update(id="c" * 32, kind="flent-run", summary={}, intervals=[], iperf_version=None)
            flent["settings"].update(engine="flent", flent_workload="rrul", streams=4, mode="bidir", label="Synthetic actual Flent format")
            native_series = [{"name": name, "unit": unit, "points": [{"seconds": i, "value": value} for i, value in enumerate(values)]}
                             for name, unit, values in (("TCP upload 1", "Mbits/s", [10, 20, None, 30]), ("TCP download 1", "Mbits/s", [50, 60, None, 70]), ("Ping (ms)", "ms", [2, 3, None, 4]))]
            flent["flent"] = {"tool": "Flent", "workload": "rrul", "version": "Synthetic fixture", "status": "captured", "data": {}, "series": native_series,
                              "summaries": [{"name": row["name"], "unit": row["unit"], "count": 3, "missing": 1, "mean": sum(p["value"] for p in row["points"] if p["value"] is not None) / 3,
                                             "min": min(p["value"] for p in row["points"] if p["value"] is not None), "max": max(p["value"] for p in row["points"] if p["value"] is not None)} for row in native_series]}
            manager.save(flent)
            for name, ids, redact in (("single", [before["id"]], False), ("comparison-redacted", [before["id"], after["id"]], True), ("flent", [flent["id"]], False)):
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
