import copy
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document

from app.exports import xml_export
from app.diagnostics import latency_summary
from app.imports import import_runs, parse_document
from app.projects import ProjectStore
from app.reports import prepare_report, render_docx, render_pdf, _chart
from app.runner import RunManager, settings_from


def report_fixture():
    baseline = [{"status": "reply", "rtt_ms": 2.1, "rtt_upper_bound_ms": None} for _ in range(3)]
    loaded = [{"status": "reply", "rtt_ms": 4.2, "rtt_upper_bound_ms": None} for _ in range(6)]
    return {"schema_version": 1, "id": "a" * 32, "created_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:00:10+00:00", "status": "completed",
            "settings": settings_from({"target": "192.0.2.10", "bind": "192.0.2.11", "notes": "private.example ticket",
                                       "label": "Private circuit", "tags": {"site": "private.example", "ticket": "Secret ticket"}}),
            "iperf_version": "iperf 3.22", "output_format": "json", "exit_code": 0,
            "command": ["iperf3", "-c", "192.0.2.10"], "error": None,
            "summary": {"sum_received": {"bits_per_second": 100e6}, "sum_sent": {"retransmits": 5}},
            "intervals": [{"sum": {"start": 0, "end": 1, "bits_per_second": 100e6}},
                          {"sum": {"start": 3, "end": 4, "bits_per_second": 120e6}}], "test_parameters": None,
            "annotations": [{"id": "b" * 32, "timestamp": "2026-01-01T10:00:03+00:00", "seconds": 3, "text": "Private note <tag>& caf\u00e9"}],
            "route": {"status": "captured", "target": "192.0.2.10", "raw_output": "private.example",
                      "hops": [{"hop": 1, "address": "192.0.2.1", "rtt_ms": [1.2, 1.3], "detail": "private.example", "responded": True}]},
            "diagnostics": {"schema_version": 1, "latency": {"enabled": True, "target": "192.0.2.10", "metric": "ICMP echo RTT",
                            "baseline": {"status": "captured", "summary": latency_summary(baseline), "samples": baseline},
                            "loaded": {"status": "captured", "summary": latency_summary(loaded), "samples": loaded},
                            "mean_increase_ms": 2.1, "error": "private.example timed out", "raw_stdout": "192.0.2.10"},
                            "endpoint_health": {"enabled": True, "local": {"provenance": "detected local", "delta": {"cpu_busy_percent": 15.2}}, "remote": {"status": "unavailable"}},
                            "path_context": {"mtu": {"status": "disabled", "tested_packet_sizes": [], "largest_confirmed_packet_bytes": None}}}}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.before = report_fixture()
        self.project = ProjectStore(self.manager).create({"name": "Private project", "description": "private.example"})
        self.before["settings"]["project_id"] = self.project["id"]
        self.manager.save(self.before)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_default_redaction_overrides_raw_and_retains_actual_measurements(self):
        self.before["diagnostics"]["latency"]["private.example"] = "Secret ticket"
        self.manager.save(self.before)
        model = prepare_report(self.manager, {"ids": [self.before["id"]], "include_raw": True})
        serialized = json.dumps(model)
        for private in ("192.0.2.10", "192.0.2.11", "192.0.2.1", "private.example", "Private circuit", "Private project", "Private note", "Secret ticket"):
            self.assertNotIn(private, serialized)
        self.assertEqual(model["runs"][0]["metrics"]["upload_mbps"], 100)
        self.assertEqual(model["runs"][0]["diagnostics"]["latency"]["loaded"]["summary"]["mean_ms"], 4.2)
        self.assertEqual(model["runs"][0]["raw_evidence"], {"status": "removed by redaction"})
        self.assertEqual(model["runs"][0]["annotations"][0]["seconds"], 3)

    def test_private_context_requires_opt_out_and_raw_separate_opt_in(self):
        model = prepare_report(self.manager, {"ids": [self.before["id"]], "redact": False})
        self.assertEqual(model["runs"][0]["settings"]["target"], "192.0.2.10")
        self.assertEqual(model["runs"][0]["project"]["name"], "Private project")
        self.assertNotIn("raw_evidence", model["runs"][0])
        self.assertNotIn("raw_output", model["runs"][0]["route"])
        self.assertNotIn("raw_stdout", model["runs"][0]["diagnostics"]["latency"])
        raw = prepare_report(self.manager, {"ids": [self.before["id"]], "redact": False, "include_raw": True})
        self.assertIn("raw_evidence", raw["runs"][0])
        self.assertEqual(raw["runs"][0]["route"]["raw_output"], "private.example")

    def test_gates_invalid_options_and_incomplete_or_parent_records(self):
        for options in ({}, {"ids": []}, {"ids": ["invalid"]}, {"ids": [self.before["id"]] * 2},
                        {"ids": [self.before["id"]], "redact": "false"}, {"ids": [self.before["id"]], "extra": 1}):
            with self.assertRaises(ValueError):
                prepare_report(self.manager, options)
        for change in ({"status": "running"}, {"status": "failed"}, {"status": "cancelled"},
                       {"kind": "matrix-job"}, {"kind": "protocol-job"}, {"summary": {}}, {"finished_at": None}):
            self.manager.save(dict(self.before, **change))
            with self.assertRaises(ValueError):
                prepare_report(self.manager, {"ids": [self.before["id"]]})
        with self.assertRaises(KeyError):
            prepare_report(self.manager, {"ids": ["f" * 32]})

    def test_comparison_warns_and_zero_baseline_percent_remains_undefined(self):
        self.before["summary"]["sum_sent"]["retransmits"] = 0
        self.manager.save(self.before)
        after = copy.deepcopy(self.before)
        after["id"] = "c" * 32
        after["settings"]["streams"] = 2
        after["summary"]["sum_received"]["bits_per_second"] = 200e6
        after["summary"]["sum_sent"]["retransmits"] = 3
        self.manager.save(after)
        model = prepare_report(self.manager, {"ids": [self.before["id"], after["id"]]})
        report = model["comparisons"][0]
        self.assertIn("streams", report["settings_differences"])
        self.assertIn("do not establish", report["caveat"])
        self.assertIsNone(next(item for item in report["metrics"] if item["metric"] == "upload_retransmits")["percent_change"])

    def test_rendered_formats_escape_text_and_are_repeatable_without_archive_writes(self):
        original = self.manager.export(self.before["id"])
        model = prepare_report(self.manager, {"ids": [self.before["id"]], "redact": False})
        for _ in range(2):
            pdf = render_pdf(model)
            self.assertTrue(pdf.startswith(b"%PDF-"))
            self.assertIn(b"/FontFile2", pdf)
            self.assertIn(b"/EmbeddedFiles", pdf)
            self.assertIn(b"Copyright (c) 2003 by Bitstream", pdf)
            word = render_docx(model)
            with ZipFile(BytesIO(word)) as archive:
                self.assertIn("word/document.xml", archive.namelist())
                self.assertIn(b"Private note &lt;tag&gt;&amp;", archive.read("word/document.xml"))
                self.assertFalse(any("vba" in name.lower() for name in archive.namelist()))
                relationships = b"".join(archive.read(name) for name in archive.namelist() if name.endswith(".rels"))
                self.assertNotIn(b'TargetMode="External"', relationships)
            document = Document(BytesIO(word))
            self.assertEqual(document.core_properties.author, "FlowBench")
            self.assertTrue(any("Mean increase ms" in cell.text for table in document.tables for row in table.rows for cell in row.cells))
        self.assertEqual(original, self.manager.export(self.before["id"]))

    def test_chart_does_not_bridge_missing_intervals(self):
        from PIL import Image
        model = prepare_report(self.manager, {"ids": [self.before["id"]]})
        image = Image.open(BytesIO(_chart(model["runs"])))
        # Seconds 2 lies in the uncaptured span between [0,1] and [3,4].
        x = int(130 + 2 / 4 * 1305)
        for y in range(80, 460):
            self.assertNotEqual(image.getpixel((x, y)), (24, 59, 80))

    def test_sqlite_restart_and_json_xml_roundtrip_preserve_report_evidence(self):
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        original = self.manager.export(self.before["id"])
        for xml in (False, True):
            with tempfile.TemporaryDirectory() as other:
                restored = RunManager(other, "missing-iperf")
                try:
                    document = parse_document(xml_export(original)) if xml else parse_document(json.dumps(original).encode())
                    imported = import_runs(restored, document, "fixture.xml" if xml else "fixture.json")
                    model = prepare_report(restored, {"ids": imported["imported"], "redact": False, "include_raw": True})
                    row = model["runs"][0]
                    self.assertEqual(row["diagnostics"], original["diagnostics"])
                    self.assertEqual(row["route"]["hops"], original["route"]["hops"])
                    self.assertEqual(row["annotations"], original["annotations"])
                    self.assertTrue(row["provenance"]["imported"])
                finally:
                    restored.shutdown()

    def test_external_series_and_metric_names_are_redacted(self):
        source = dict(self.before, kind="flent-run", flent={"version": "private.example version",
                      "series": [{"name": "TCP upload private.example", "unit": "Mbits/s", "points": [{"seconds": 1, "value": 10}, {"seconds": 2, "value": None}]}],
                      "summaries": [{"name": "TCP upload private.example", "unit": "Mbits/s", "count": 1, "missing": 1, "mean": 10, "min": 10, "max": 10}]})
        self.manager.save(source)
        with patch("app.reports.measurements", return_value={"flent_TCP upload private.example_mbps": 10}):
            model = prepare_report(self.manager, {"ids": [source["id"]]})
        self.assertNotIn("private.example", json.dumps(model))
        self.assertEqual(model["runs"][0]["flent_series"][0]["points"][1]["value"], None)
        self.assertEqual(model["runs"][0]["flent_summaries"][0]["missing"], 1)

    def test_preview_fingerprint_accepts_same_snapshot_and_rejects_changes(self):
        options = {"ids": [self.before["id"]]}
        first = prepare_report(self.manager, options)
        second = prepare_report(self.manager, dict(options, fingerprint=first["fingerprint"]))
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        changed = copy.deepcopy(self.before)
        changed["summary"]["sum_received"]["bits_per_second"] *= 2
        self.manager.save(changed)
        with self.assertRaisesRegex(ValueError, "Saved evidence changed"):
            prepare_report(self.manager, dict(options, fingerprint=first["fingerprint"]))

    def test_concurrent_report_requests_keep_archive_and_models_unchanged(self):
        before = self.manager.export(self.before["id"])
        def generate(index):
            model = prepare_report(self.manager, {"ids": [self.before["id"]]})
            pdf, word = render_pdf(model), render_docx(model)
            self.assertTrue(pdf.startswith(b"%PDF-"))
            with ZipFile(BytesIO(word)) as package:
                self.assertIsNone(package.testzip())
            return model["fingerprint"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            fingerprints = list(pool.map(generate, range(4)))
        self.assertEqual(len(set(fingerprints)), 1)
        self.assertEqual(self.manager.export(self.before["id"]), before)


if __name__ == "__main__":
    unittest.main()
