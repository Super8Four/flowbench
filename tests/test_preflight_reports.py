"""Imported local-clock evidence stays portable, bounded and explicitly unverified."""
import copy
from io import BytesIO
import json
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from app import create_app
from app.exports import xml_export
from app.geolocation import empty_location, geolocation_metadata
from app.reports import prepare_report, render_pdf, render_docx, _sections, _world_map, _route_rows
from app.time_quality import time_quality_result
from tests.test_reports import report_fixture, decoded_pdf_streams


def fixture():
    record = report_fixture()
    quality = time_quality_result("captured")
    quality.update(observed_at="2026-01-01T09:59:58+00:00", provider="chrony", source="clock-source.example <source>&",
                   synchronized=True, offset_seconds=-.0000004, uncertainty_seconds=0,
                   uncertainty_kind="root_dispersion_proxy", root_delay_seconds=.002, root_dispersion_seconds=0,
                   last_sync_at="2026-01-01T09:59:50Z", query_elapsed_seconds=.05,
                   note="private-clock-note.example <text>&", raw_output="private-clock-raw.example",
                   command=["/private-clock-path.example/chronyc", "-n", "-c", "-h", "127.0.0.1", "tracking"])
    record["preflight"] = {"schema_version": 1, "policy": "once_per_test", "status": "captured", "phase": "before_traffic",
                           "started_at": "2026-01-01T09:59:58+00:00", "finished_at": "2026-01-01T09:59:59+00:00",
                           "elapsed_seconds": 1, "time_quality": quality}
    record["route"] = {"status": "captured", "phase": "before_traffic", "started_at": "2026-01-01T09:59:58+00:00",
                       "finished_at": "2026-01-01T09:59:58.600000+00:00", "elapsed_seconds": .6,
                       "raw_output": "private-route-output.example", "target": "192.0.2.10",
                       "command": ["/private-route-path.example/tracepath", "-n", "-m", "16", "192.0.2.10"],
                       "hops": [{"hop": 1, "address": "192.0.2.42", "addresses": ["192.0.2.42", "192.0.2.43"],
                                 "rtt_ms": [0, 1.25], "rtt_upper_bound_ms": [1], "responded": True, "observed": True,
                                 "detail": "private-route-detail.example <text>&",
                                 "observations": [{"addresses": ["192.0.2.42"], "rtt_ms": [1.25], "rtt_upper_bound_ms": [],
                                                   "detail": "private-route-observation.example"}]},
                                {"hop": 2, "address": None, "addresses": [], "rtt_ms": [], "responded": False, "observed": True}]}
    return record


def geographic_fixture():
    record = fixture()
    geo = geolocation_metadata("available", "Synthetic geolocation fixture only.")
    geo.update(database_type="DBIP-City-Lite", build_epoch=1767225600)
    locations = []
    for address, lat, lon in (("8.8.8.8", 12.345678, 23.456789), ("2001:4860:4860::8888", -25.5, 130.25)):
        row = empty_location(address)
        row.update(status="located", city="private-city.example <city>&", country="private-country.example", country_code="ZZ",
                   latitude=lat, longitude=lon)
        locations.append(row)
    record["route"]["geolocation"] = geo
    record["route"]["hops"] = [
        {"hop": 1, "address": "192.168.1.1", "addresses": ["192.168.1.1"], "rtt_ms": [0], "rtt_upper_bound_ms": [1],
         "responded": True, "observed": True, "locations": [empty_location("192.168.1.1")]},
        {"hop": 2, "address": "8.8.8.8", "addresses": ["8.8.8.8", "2001:4860:4860::8888"], "rtt_ms": [2],
         "responded": True, "observed": True, "locations": locations},
        {"hop": 3, "address": None, "addresses": [], "rtt_ms": [], "responded": False, "observed": False,
         "locations": [empty_location(None)]}]
    return record


class PreflightReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.open()

    def open(self):
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": "missing-iperf"})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["iperf_runs"]

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def upload(self, source, xml=False):
        return self.client.post("/api/runs/import", json={"filename": "synthetic.xml" if xml else "synthetic.json",
                                "content": xml_export(source).decode() if xml else json.dumps(source)})

    def test_sqlite_reopen_json_xml_roundtrip_preserves_exact_source_and_measurements(self):
        for xml in (False, True):
            with self.subTest(xml=xml), patch("app.time_quality.capture_time_quality") as query:
                source = geographic_fixture()
                source["id"] = ("b" if xml else "a") * 32
                response = self.upload(source, xml)
                self.assertEqual(response.status_code, 200, response.get_json())
                run_id = (response.get_json()["imported"] or response.get_json()["duplicates"])[0]
                saved = self.manager.export(run_id)
                self.assertEqual(saved["preflight"], source["preflight"])
                self.assertEqual(saved["route"], source["route"])
                self.assertEqual(saved["metrics"]["upload_mbps"], 100)
                self.manager.shutdown()
                self.open()
                self.assertEqual(saved, self.manager.export(run_id))
                query.assert_not_called()
                self.assertIsNone(self.manager.active)

    def test_geolocation_privacy_and_world_map_use_only_valid_approximate_points(self):
        from PIL import Image, ImageColor
        for xml in (False, True):
            source = geographic_fixture()
            source["id"] = ("c" if xml else "d") * 32
            response = self.upload(source, xml)
            self.assertEqual(response.status_code, 200, response.get_json())
            run_id = response.get_json()["imported"][0]
            original = self.manager.export(run_id)
            redacted = prepare_report(self.manager, {"ids": [run_id], "include_raw": True})
            private = prepare_report(self.manager, {"ids": [run_id], "redact": False})
            self.assertNotIn("geolocation", redacted["runs"][0]["route"])
            self.assertTrue(all("locations" not in hop for hop in redacted["runs"][0]["route"]["hops"]))
            self.assertIsNone(_world_map(redacted["runs"][0]))
            world = Image.open(BytesIO(_world_map(private["runs"][0])))
            x = round(60 + (23.456789 + 180) / 360 * 1380)
            y = round(45 + (90 - 12.345678) / 180 * 690)
            self.assertEqual(world.getpixel((x, y)), ImageColor.getrgb(private["runs"][0]["color"]))
            outputs = [json.dumps(redacted).encode(), decoded_pdf_streams(render_pdf(redacted))]
            with ZipFile(BytesIO(render_docx(redacted))) as package:
                outputs.append(b"\n".join(package.read(name) for name in package.namelist() if name.endswith(".xml")))
                redacted_images = len([name for name in package.namelist() if name.startswith("word/media/")])
            for text in outputs:
                for secret in (b"private-city", b"private-country", b"12.345678", b"23.456789", b"12.3457", b"23.4568", b"DB-IP", b"CC-BY", b"8.8.8.8", b"2001:4860", b"192.168.1.1"):
                    self.assertNotIn(secret, text)
            with ZipFile(BytesIO(render_docx(private))) as package:
                text = package.read("word/document.xml")
                self.assertIn(b"private-city.example &lt;city&gt;&amp;", text)
                self.assertIn(b"https://db-ip.com", text)
                self.assertIn(b"CC-BY-4.0", text)
                self.assertIn(b"Approximate public-IP world map", text)
                self.assertIn(b"Unknown location", text)
                self.assertGreater(len([name for name in package.namelist() if name.startswith("word/media/")]), redacted_images)
            self.assertEqual(original, self.manager.export(run_id))

    def test_invalid_geo_batch_is_atomic_including_internal_coords_and_wrong_address_links(self):
        for xml in (False, True):
            for mutate in (lambda route: route["hops"][0]["locations"][0].update(latitude=1, longitude=2),
                           lambda route: route["hops"][1]["locations"][0].update(latitude=True),
                           lambda route: route["hops"][1]["locations"][0].update(longitude=181),
                           lambda route: route["hops"][1]["locations"].reverse(),
                           lambda route: route["geolocation"].update(attribution_url="https://private-provider.example")):
                broken = geographic_fixture()
                broken["id"] = "b" * 32
                mutate(broken["route"])
                response = self.upload({"before": geographic_fixture(), "after": broken}, xml)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(self.manager.list_runs())

    def test_malformed_batch_never_writes_partial_evidence_or_claims_ntp_verification(self):
        cases = [("ntp_verified", True), ("clock_source", "verified-private.example"), ("provider", "provider-private.example"),
                 ("status", "private-status.example"), ("offset_seconds", True), ("offset_seconds", float("inf")),
                 ("uncertainty_seconds", -1), ("observed_at", "2026-01-01T10:00:00"),
                 ("last_sync_at", "private-timestamp.example"), ("raw_output", "x" * 8193),
                 ("command", ["chronyc", "-h", "external.example", "tracking"])]
        for xml in (False, True):
            for field, value in cases:
                with self.subTest(xml=xml, field=field):
                    broken = fixture()
                    broken["id"] = "b" * 32
                    broken["preflight"]["time_quality"][field] = value
                    response = self.upload({"before": fixture(), "after": broken}, xml)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(self.manager.list_runs())
            for field, value in (("phase", "unrecorded-private.example"), ("elapsed_seconds", -1),
                                 ("started_at", "2026-01-01T09:59:58+03:00")):
                broken = fixture()
                broken["preflight"][field] = value
                self.assertEqual(self.upload({"before": fixture(), "after": broken}, xml).status_code, 400)
                self.assertFalse(self.manager.list_runs())
            for mutate in (lambda route: route["hops"].reverse(),
                           lambda route: route["hops"][0].update(addresses=["private-route-host.example"]),
                           lambda route: route["hops"][0].update(rtt_ms=[True]),
                           lambda route: route.update(command=["external-command.example"]),
                           lambda route: route.update(elapsed_seconds=-1)):
                broken = fixture()
                mutate(broken["route"])
                self.assertEqual(self.upload({"before": fixture(), "after": broken}, xml).status_code, 400)
                self.assertFalse(self.manager.list_runs())

    def test_redacted_import_preview_pdf_docx_remove_source_context_preserve_signed_small_values(self):
        for xml in (False, True):
            response = self.upload(fixture(), xml)
            self.assertEqual(response.status_code, 200, response.get_json())
            run_id = (response.get_json()["imported"] or response.get_json()["duplicates"])[0]
            original = self.manager.export(run_id)
            preview = self.client.post("/api/reports/preview", json={"ids": [run_id], "include_raw": True})
            self.assertEqual(preview.status_code, 200, preview.get_json())
            model = preview.get_json()
            quality = model["runs"][0]["preflight"]["time_quality"]
            self.assertEqual(quality["offset_seconds"], -.0000004)
            self.assertEqual(quality["uncertainty_seconds"], 0)
            self.assertIs(quality["ntp_verified"], False)
            self.assertEqual(quality["observed_at"], "2026-01-01T09:59:58+00:00")
            self.assertEqual(model["runs"][0]["route"]["phase"], "before_traffic")
            self.assertEqual(model["runs"][0]["route"]["hops"][0]["rtt_ms"], [0, 1.25])
            outputs = [json.dumps(model).encode()]
            for format_name in ("pdf", "docx"):
                downloaded = self.client.post("/api/reports/download?format=" + format_name,
                                              json={"ids": [run_id], "include_raw": True, "fingerprint": model["fingerprint"]})
                self.assertEqual(downloaded.status_code, 200)
                if format_name == "pdf":
                    text = decoded_pdf_streams(downloaded.data)
                else:
                    with ZipFile(BytesIO(downloaded.data)) as package:
                        text = b"\n".join(package.read(name) for name in package.namelist() if name.endswith(".xml"))
                outputs.append(text)
                self.assertIn(b"not independently NTP verified", text)
                self.assertIn(b"-4e-07", text)
            for text in outputs:
                for secret in (b"clock-source.example", b"private-clock", b"private-route", b"192.0.2.42", b"192.0.2.43", b"127.0.0.1"):
                    self.assertNotIn(secret, text)
            self.assertEqual(original, self.manager.export(run_id))

    def test_private_text_is_literal_opt_in_and_raw_still_separate(self):
        response = self.upload(fixture())
        self.assertEqual(response.status_code, 200, response.get_json())
        run_id = response.get_json()["imported"][0]
        model = prepare_report(self.manager, {"ids": [run_id], "redact": False})
        quality = model["runs"][0]["preflight"]["time_quality"]
        self.assertEqual(quality["source"], "clock-source.example <source>&")
        self.assertNotIn("raw_output", quality)
        self.assertNotIn("command", quality)
        self.assertEqual(_route_rows(model["runs"][0])[1], ["1 (obs 1)", "192.0.2.42", "1.25"])
        raw = prepare_report(self.manager, {"ids": [run_id], "redact": False, "include_raw": True})
        self.assertEqual(raw["runs"][0]["preflight"]["time_quality"]["raw_output"], "private-clock-raw.example")
        with ZipFile(BytesIO(render_docx(model))) as package:
            document = package.read("word/document.xml")
            self.assertIn(b"clock-source.example &lt;source&gt;&amp;", document)
            self.assertIn(b"192.0.2.42, 192.0.2.43", document)
            self.assertIn(b"&lt;1", document)
            rels = b"".join(package.read(name) for name in package.namelist() if name.endswith(".rels"))
            self.assertNotIn(b'TargetMode="External"', rels)
        self.assertIn(b"clock-source.example", decoded_pdf_streams(render_pdf(model)))

    def test_legacy_and_status_only_clock_evidence_never_invent_accuracy(self):
        source = report_fixture()
        self.manager.save(source)
        legacy = prepare_report(self.manager, {"ids": [source["id"]]})
        sections = json.dumps(_sections(legacy["runs"][0]))
        self.assertIn("not recorded", sections)
        self.assertIn("not independently NTP verified", sections)
        for status in ("status_only", "unavailable", "unsynchronized"):
            source = fixture()
            source["preflight"]["time_quality"] = time_quality_result(status)
            if status in {"status_only", "unsynchronized"}:
                source["preflight"]["time_quality"].update(provider="systemd", synchronized=status == "status_only",
                    command=["timedatectl", "show", "--property=NTPSynchronized"])
            response = self.upload(source)
            self.assertEqual(response.status_code, 200, response.get_json())
            model = prepare_report(self.manager, {"ids": response.get_json()["imported"]})
            quality = model["runs"][0]["preflight"]["time_quality"]
            self.assertEqual(quality["status"], status)
            self.assertIsNone(quality["offset_seconds"])
            self.assertIsNone(quality["uncertainty_seconds"])
            self.assertIs(quality["ntp_verified"], False)


if __name__ == "__main__":
    unittest.main()
