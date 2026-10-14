import os
import json
from pathlib import Path
from urllib.parse import urlsplit

import click
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

from .exports import xml_export
from .runner import RunManager
from .metrics import comparison, live_comparison, measurements, series, effective_settings, overview
from .imports import parse_document, import_runs, prepare_source_documents
from .presets import PresetStore, MAX_CATALOG_BYTES
from .peer import peer_results
from .listener import IncomingListener, ListenerConflict
from .version import __version__
from .projects import ProjectStore
from .diagnostics import diagnostic_capabilities
from .units import RATE_CONTRACT


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config["RUNS_DIRECTORY"] = os.getenv("RUNS_DIRECTORY", str(Path(app.instance_path) / "runs"))
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024
    app.config["FLOWBENCH_VERSION"] = __version__
    if test_config:
        app.config.update(test_config)
    manager = RunManager(app.config["RUNS_DIRECTORY"], app.config.get("IPERF_PATH"))
    app.extensions["iperf_runs"] = manager
    presets = PresetStore(manager)
    app.extensions["iperf_presets"] = presets
    projects = ProjectStore(manager)
    app.extensions["iperf_projects"] = projects
    listener = IncomingListener(manager)
    manager.listener = listener
    app.extensions["iperf_listener"] = listener
    if listener.config["enabled"] and os.getenv("FLOWBENCH_RESUME_LISTENER", "1") != "0":
        try:
            listener.start(persist=False)
        except (OSError, RuntimeError, ValueError) as exc:
            listener.state, listener.error = "failed", str(exc)

    @app.before_request
    def check_origin():
        # Local control API: reject browser writes originating from another site.
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="Cross-origin writes are not allowed."), 403
            if not request.is_json:
                return jsonify(error="Use application/json for control requests."), 415
            if manager.shutdown_requested or manager.storage_lock.closed:
                return jsonify(error="This archive process is shutting down. Use the current app process before making changes."), 503

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(KeyError)
    def missing_run(error):
        return jsonify(error="Run not found."), 404

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error="Upload request exceeds 32 MiB. Choose a file up to 20 MiB."), 413

    @app.errorhandler(OSError)
    def storage_failure(error):
        return jsonify(error=f"Storage operation failed: {error}"), 503

    def execution_failure(error, conflict=False):
        from .reruns import AdmissionConflict, AdmissionUnavailable
        code = (409 if isinstance(error, AdmissionConflict) else
                503 if isinstance(error, AdmissionUnavailable) else 409 if conflict else 503)
        payload = {"error": str(error)}
        if getattr(error, "record", None) is not None:
            payload["run"] = error.record
        return jsonify(payload), code

    def uploaded_document(limit):
        data = request.get_json()
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            raise ValueError("Send the uploaded file as content text in a JSON object.")
        filename = data.get("filename", "import.json")
        if not isinstance(filename, str) or len(filename) > 255:
            raise ValueError("Invalid uploaded filename.")
        try:
            content = data["content"].encode("utf-8")
        except UnicodeError:
            raise ValueError("File must contain valid UTF-8 text.") from None
        if len(content) > limit:
            raise ValueError(f"File exceeds {limit // (1024 * 1024)} MiB.")
        return parse_document(content), filename.replace("\\", "/").split("/")[-1]

    @app.get("/api/projects")
    def list_projects():
        return jsonify(projects=projects.list())

    @app.post("/api/projects")
    def create_project():
        try:
            return jsonify(projects.create(request.get_json())), 201
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503

    @app.patch("/api/projects/<project_id>")
    def edit_project(project_id):
        try:
            return jsonify(projects.update(project_id, request.get_json()))
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503

    @app.get("/api/projects/<project_id>")
    def project_report(project_id):
        return jsonify(projects.report(project_id))

    @app.get("/api/projects/<project_id>/export")
    def export_project(project_id):
        format_name = request.args.get("format", "json").lower()
        if format_name not in {"json", "xml"}:
            raise ValueError("Choose json or xml.")
        document = projects.export(project_id)
        payload = json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) if format_name == "json" else xml_export(document)
        return Response(payload, mimetype="application/" + format_name,
                        headers={"Content-Disposition": f'attachment; filename="flowbench-project-{project_id}.{format_name}"',
                                 "Cache-Control": "no-store"})

    @app.post("/api/projects/import")
    def import_project():
        data, filename = uploaded_document(20 * 1024 * 1024)
        try:
            return jsonify(projects.import_bundle(data, filename, prepare_source_documents))
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503

    @app.get("/api/presets")
    def get_presets():
        data = presets.read()
        if request.args.get("download") == "1":
            return Response(json.dumps(data, indent=2, ensure_ascii=False), mimetype="application/json",
                            headers={"Content-Disposition": 'attachment; filename="iperf-server-presets.json"'})
        return jsonify(data)

    @app.get("/api/listener")
    def listener_status():
        return jsonify(listener.status())

    @app.post("/api/listener")
    def start_listener():
        try:
            return jsonify(listener.start(request.get_json())), 201
        except ListenerConflict as exc:
            return jsonify(error=str(exc)), 409
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503

    @app.post("/api/listener/stop")
    def stop_listener():
        return jsonify(listener.stop())

    @app.post("/api/presets")
    def save_preset():
        return jsonify(presets.save_custom(request.get_json())), 201

    @app.patch("/api/presets/<preset_id>")
    def update_preset(preset_id):
        return jsonify(presets.save_custom(request.get_json(), preset_id))

    @app.delete("/api/presets/<preset_id>")
    def remove_preset(preset_id):
        presets.delete_custom(preset_id)
        return jsonify(status="removed")

    @app.post("/api/presets/import")
    def import_presets():
        data, _ = uploaded_document(MAX_CATALOG_BYTES)
        return jsonify(presets.merge(data))

    @app.post("/api/presets/refresh")
    def refresh_presets():
        try:
            return jsonify(presets.refresh())
        except (OSError, TimeoutError) as exc:
            return jsonify(error=f"Public directory refresh failed; the saved catalog is retained. {exc}"), 502

    @app.post("/api/runs/import")
    def import_saved_tests():
        data, filename = uploaded_document(20 * 1024 * 1024)
        try:
            return jsonify(import_runs(manager, data, filename))
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503

    @app.get("/")
    @app.get("/history")
    @app.get("/comparison")
    @app.get("/servers")
    @app.get("/server")
    @app.get("/configuration")
    @app.get("/projects")
    def index():
        page = request.path.strip("/") or "test"
        return render_template("index.html", page=page)

    @app.get("/api/status")
    def status():
        from .checks import check_capabilities
        return jsonify(iperf=manager.capabilities, active_run=manager.active_id,
                       storage=str(manager.directory), storage_error=manager.storage_error,
                       storage_backend="sqlite", database=str(manager.storage.path),
                       saved_runs=len(manager.list_runs()), version=__version__, listener=listener.status(),
                       diagnostics=diagnostic_capabilities(), flent=manager.flent_capabilities,
                       checks=check_capabilities())

    @app.get("/api/runs")
    def list_runs():
        return jsonify(runs=manager.list_runs(), active_run=manager.active_id, rate_units=RATE_CONTRACT)

    @app.get("/api/ready")
    def readiness():
        try:
            ready = bool(manager.capabilities["available"] and not manager.storage_error and not manager.shutdown_requested
                         and not manager.storage_lock.closed and manager.storage.ready())
        except OSError:
            ready = False
        return jsonify(ready=ready), 200 if ready else 503

    @app.post("/api/runs")
    def start_run():
        try:
            record = manager.start(request.get_json())
        except RuntimeError as exc:
            data = request.get_json()
            conflict = manager.active or (listener.running and isinstance(data, dict) and data.get("role") == "server")
            return execution_failure(exc, bool(conflict))
        return jsonify(record), 201

    @app.post("/api/matrices")
    def start_matrix():
        try:
            return jsonify(manager.start_matrix(request.get_json())), 201
        except RuntimeError as exc:
            return execution_failure(exc, bool(manager.active))

    @app.get("/api/flent")
    def flent_status():
        return jsonify(manager.flent_capabilities)

    @app.post("/api/flent")
    def start_flent():
        try:
            return jsonify(manager.start_flent(request.get_json())), 201
        except RuntimeError as exc:
            return execution_failure(exc, bool(manager.active))

    @app.get("/api/checks")
    def check_status():
        from .checks import check_capabilities
        return jsonify(check_capabilities())

    @app.post("/api/checks")
    def start_check():
        try:
            return jsonify(manager.start_check(request.get_json())), 201
        except RuntimeError as exc:
            return execution_failure(exc, bool(manager.active))

    @app.get("/api/runs/<run_id>/rerun-plan")
    def rerun_plan(run_id):
        try:
            plan = manager.rerun_plan(run_id)
        except RuntimeError as exc:
            return execution_failure(exc, bool(manager.active))
        response = jsonify(plan)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/runs/<run_id>/rerun")
    def rerun(run_id):
        try:
            result = manager.rerun(run_id, request.get_json())
            return jsonify(result), 200 if result["replayed"] else 201
        except RuntimeError as exc:
            return execution_failure(exc, bool(manager.active))

    @app.get("/api/runs/<run_id>")
    def get_run(run_id):
        after = request.args.get("after", "0")
        if not after.isdigit():
            raise ValueError("after must be a non-negative event sequence number.")
        record = manager.read(run_id)
        record["rate_units"] = RATE_CONTRACT
        record["metrics"] = measurements(record)
        record["series"] = series(record)
        record["overview"] = overview(record)
        record["effective_settings"] = effective_settings(record)
        record["peer"] = peer_results(record)
        record["events"] = manager.read_events(run_id, int(after))
        if record.get("kind") == "protocol-job":
            record["protocol_runs"] = manager.protocol_runs(record)
        if record.get("kind") == "matrix-job":
            record["matrix_runs"] = manager.protocol_runs(record)
        return jsonify(record)

    @app.post("/api/runs/<run_id>/stop")
    def stop_run(run_id):
        manager.stop(run_id)
        return jsonify(status="stop_requested")

    @app.patch("/api/runs/<run_id>")
    def annotate_run(run_id):
        try:
            return jsonify(manager.annotate(run_id, request.get_json()))
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503

    @app.get("/api/runs/<run_id>/export")
    def export_run(run_id):
        format_name = request.args.get("format", "json").lower()
        if format_name not in {"json", "xml"}:
            raise ValueError("Choose json or xml.")
        data = manager.export(run_id)
        payload = (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)
                   if format_name == "json" else xml_export(data))
        return Response(payload, mimetype="application/" + format_name,
                        headers={"Content-Disposition": f'attachment; filename="iperf-{run_id}.{format_name}"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/live-comparison")
    def compare_active():
        return jsonify(live_comparison(manager.read(request.args.get("baseline", "")),
                                       manager.read(request.args.get("active", ""))))

    @app.post("/api/reports/preview")
    def preview_report():
        from .reports import prepare_report
        return jsonify(prepare_report(manager, request.get_json()))

    @app.post("/api/reports/download")
    def download_report():
        from .reports import prepare_report, render_pdf, render_docx
        format_name = request.args.get("format", "pdf").lower()
        if format_name not in {"pdf", "docx"}:
            raise ValueError("Choose pdf or docx.")
        model = prepare_report(manager, request.get_json())
        payload = render_pdf(model) if format_name == "pdf" else render_docx(model)
        mimetype = "application/pdf" if format_name == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return Response(payload, mimetype=mimetype,
                        headers={"Content-Disposition": f'attachment; filename="flowbench-report.{format_name}"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/comparison")
    def compare_runs():
        from .comparisons import common_window, MAX_COMPARISON_BYTES
        from .units import RATE_CONTRACT, run_color

        format_name = request.args.get("format")
        if format_name and format_name not in {"json", "xml"}:
            raise ValueError("Choose json or xml.")

        def respond(report):
            report["rate_units"] = RATE_CONTRACT
            payload = (json.dumps(report, indent=2 if format_name else None, ensure_ascii=False, allow_nan=False).encode("utf-8")
                       if format_name != "xml" else xml_export(report))
            if len(payload) > MAX_COMPARISON_BYTES:
                return jsonify(error="Comparison exceeds the 32 MiB response budget. Select fewer tests or export individual runs."), 413
            headers = {"Cache-Control": "no-store"}
            if format_name:
                headers["Content-Disposition"] = f'attachment; filename="iperf-comparison.{format_name}"'
            return Response(payload, mimetype="application/" + (format_name or "json"), headers=headers)

        ids = request.args.getlist("id")
        if ids:
            if len(ids) < 2 or len(set(ids)) != len(ids):
                raise ValueError("Choose at least two different completed individual tests.")
            with manager.lock:
                records = [manager.read(run_id) for run_id in ids]
                if any(record.get("children") or record.get("kind") in {"protocol-job", "matrix-job"} for record in records):
                    raise ValueError("Choose completed individual tests rather than complete jobs.")
                reports = [comparison(records[0], record) for record in records[1:]]
                selected_runs = []
                for record in records:
                    if format_name:
                        selected_runs.append(manager.export(record["id"]))
                    else:
                        selected_runs.append({"id": record["id"], "status": record["status"],
                                              "created_at": record["created_at"], "settings": record["settings"],
                                              "color": run_color(record), "imported": record.get("imported"),
                                              "metrics": measurements(record), "series": series(record),
                                              "effective_settings": effective_settings(record), "peer": peer_results(record)})
                window = common_window(records)
            report = {"schema_version": 1, "kind": "multi-comparison", "comparisons": reports,
                      "runs": selected_runs, "aggregate_scope": "whole_run",
                      "common_window_seconds": window["seconds"], "common_window": window}
            return respond(report)
        with manager.lock:
            before = manager.read(request.args.get("before", ""))
            after = manager.read(request.args.get("after", ""))
            if any(record.get("children") or record.get("kind") in {"protocol-job", "matrix-job"} for record in (before, after)):
                raise ValueError("Choose completed individual tests rather than complete jobs.")
            report = comparison(before, after)
            if format_name:
                report["before"] = manager.export(before["id"])
                report["after"] = manager.export(after["id"])
        return respond(report)

    @app.get("/api/check-comparison")
    def compare_checks():
        from .comparisons import MAX_COMPARISON_BYTES
        from .metrics import check_comparison

        format_name = request.args.get("format")
        if format_name and format_name not in {"json", "xml"}:
            raise ValueError("Choose json or xml.")
        ids = request.args.getlist("id")
        if not ids:
            ids = [request.args.get("before", ""), request.args.get("after", "")]
        if len(ids) < 2 or len(set(ids)) != len(ids) or any(not value for value in ids):
            raise ValueError("Choose at least two different saved diagnostic checks of the same type.")
        with manager.lock:
            records = []
            source_bytes = 0
            for run_id in ids:
                record = manager.read(run_id)
                if record.get("kind") != "diagnostic-check":
                    raise ValueError("Diagnostic comparisons require DNS or ICMP checks; keep iPerf and Flent measurements separate.")
                source_bytes += len(json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                if source_bytes > MAX_COMPARISON_BYTES:
                    return jsonify(error="Comparison exceeds the 32 MiB response budget. Select fewer checks."), 413
                records.append(record)
            comparisons = [check_comparison(records[0], record) for record in records[1:]]
            selected, selected_bytes = [], 0
            for record in records:
                snapshot = (manager.export(record["id"]) if format_name else
                            {key: record.get(key) for key in
                             ("id", "kind", "check_type", "status", "created_at", "started_at", "finished_at",
                              "settings", "result", "time_quality", "lineage", "imported", "annotations")})
                selected_bytes += len(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8"))
                if selected_bytes > MAX_COMPARISON_BYTES:
                    return jsonify(error="Comparison exceeds the 32 MiB response budget. Select fewer checks."), 413
                selected.append(snapshot)
        report = {"schema_version": 1, "kind": "diagnostic-comparison", "check_type": records[0]["check_type"],
                  "runs": selected, "comparisons": comparisons,
                  "interpretation": "Each check retains its own outcome and timing. DNS answers and ICMP echo nonresponse do not establish application latency or path packet loss."}
        payload = (json.dumps(report, indent=2 if format_name else None, ensure_ascii=False, allow_nan=False).encode("utf-8")
                   if format_name != "xml" else xml_export(report))
        if len(payload) > MAX_COMPARISON_BYTES:
            return jsonify(error="Comparison exceeds the 32 MiB response budget. Select fewer checks."), 413
        headers = {"Cache-Control": "no-store"}
        if format_name:
            headers["Content-Disposition"] = f'attachment; filename="flowbench-check-comparison.{format_name}"'
        return Response(payload, mimetype="application/" + (format_name or "json"), headers=headers)

    @app.get("/api/health")
    def health():
        try:
            if manager.storage_lock.closed or manager.storage_error:
                raise OSError("Archive unavailable")
            manager.storage.ready()
            return jsonify(app="ok", database="connected")
        except OSError:
            return jsonify(app="ok", database="unavailable"), 503

    @app.cli.command("init-db")
    def init_db():
        """Check the SQLite archive (tables and legacy import initialize at startup)."""
        manager.storage.ready()
        click.echo(f"SQLite archive ready: {manager.storage.path}")

    @app.cli.command("export-legacy")
    @click.argument("destination", type=click.Path())
    def export_legacy(destination):
        """Export a rollback snapshot into a new, non-existing directory."""
        with manager.lock:
            if manager.active:
                raise click.ClickException("Stop the active test before exporting a rollback archive.")
            manager.storage.export_legacy(destination)
        click.echo(f"Legacy archive exported to {destination}")

    return app
