import os
import json
from pathlib import Path
from urllib.parse import urlsplit

import click
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .exports import xml_export
from .runner import RunManager
from .metrics import comparison, measurements, series, effective_settings

db = SQLAlchemy()


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://iperf:change-me@localhost:5432/iperf"
    )
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 3},
    }
    app.config["RUNS_DIRECTORY"] = os.getenv("RUNS_DIRECTORY", str(Path(app.instance_path) / "runs"))
    if test_config:
        app.config.update(test_config)
    db.init_app(app)
    manager = RunManager(app.config["RUNS_DIRECTORY"], app.config.get("IPERF_PATH"))
    app.extensions["iperf_runs"] = manager

    @app.before_request
    def check_origin():
        # Local control API: reject browser writes originating from another site.
        if request.method in {"POST", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="Cross-origin writes are not allowed."), 403
            if not request.is_json:
                return jsonify(error="Use application/json for control requests."), 415

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(KeyError)
    def missing_run(error):
        return jsonify(error="Run not found."), 404

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/status")
    def status():
        return jsonify(iperf=manager.capabilities, active_run=manager.active,
                       storage=str(manager.directory), storage_error=manager.storage_error,
                       saved_runs=len(manager.list_runs()))

    @app.get("/api/runs")
    def list_runs():
        return jsonify(runs=manager.list_runs(), active_run=manager.active)

    @app.post("/api/runs")
    def start_run():
        try:
            record = manager.start(request.get_json())
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 409 if manager.active else 503
        return jsonify(record), 201

    @app.get("/api/runs/<run_id>")
    def get_run(run_id):
        after = request.args.get("after", "0")
        if not after.isdigit():
            raise ValueError("after must be a non-negative event sequence number.")
        record = manager.read(run_id)
        record["metrics"] = measurements(record)
        record["series"] = series(record)
        record["effective_settings"] = effective_settings(record)
        record["events"] = manager.read_events(run_id, int(after))
        return jsonify(record)

    @app.post("/api/runs/<run_id>/stop")
    def stop_run(run_id):
        manager.stop(run_id)
        return jsonify(status="stop_requested")

    @app.patch("/api/runs/<run_id>")
    def annotate_run(run_id):
        return jsonify(manager.annotate(run_id, request.get_json()))

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

    @app.get("/api/comparison")
    def compare_runs():
        before = manager.read(request.args.get("before", ""))
        after = manager.read(request.args.get("after", ""))
        report = comparison(before, after)
        format_name = request.args.get("format")
        if not format_name:
            return jsonify(report)
        if format_name not in {"json", "xml"}:
            raise ValueError("Choose json or xml.")
        report["before"] = manager.export(before["id"])
        report["after"] = manager.export(after["id"])
        payload = json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) if format_name == "json" else xml_export(report)
        return Response(payload, mimetype="application/" + format_name,
                        headers={"Content-Disposition": f'attachment; filename="iperf-comparison.{format_name}"',
                                 "Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(app="ok", database="connected")
        except SQLAlchemyError:
            db.session.rollback()
            return jsonify(app="ok", database="unavailable"), 503

    @app.cli.command("init-db")
    def init_db():
        """Create tables registered with SQLAlchemy."""
        from . import models  # noqa: F401

        db.create_all()
        click.echo("Database tables created.")

    return app
