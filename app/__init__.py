import os

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

db = SQLAlchemy()


def create_app():
    load_dotenv()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://iperf:change-me@localhost:5432/iperf"
    )
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "connect_args": {"connect_timeout": 3},
    }
    db.init_app(app)

    @app.get("/")
    def index():
        return render_template("index.html")

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
