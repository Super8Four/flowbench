"""SQLite archive, using Python's bundled driver on Windows and Linux."""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import threading


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class SQLiteArchive:
    def __init__(self, directory, timeout=5):
        self.directory = Path(directory)
        self.path = self.directory / "archive.sqlite3"
        self.timeout = timeout
        # Include connection close/checkpoint in serialization. Older bundled
        # SQLite releases have a WAL reset race with overlapping checkpoints.
        self.lock = threading.RLock()
        with self.connection() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise OSError(f"Unsupported archive schema {version}; use a compatible app version.")
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise OSError("The archive filesystem does not support SQLite WAL.")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, document TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL REFERENCES runs(id), sequence INTEGER NOT NULL, document TEXT NOT NULL, PRIMARY KEY(run_id, sequence))")
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("PRAGMA user_version=1")
            connection.commit()

    @contextmanager
    def connection(self):
        with self.lock:
            with self._connection() as connection:
                yield connection

    @contextmanager
    def _connection(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        except sqlite3.Error as exc:
            raise OSError(f"SQLite archive operation failed: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def transaction(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def records(self, connection=None):
        if connection is None:
            with self.connection() as connection:
                return self.records(connection)
        return [json.loads(row[0]) for row in connection.execute("SELECT document FROM runs ORDER BY id")]

    def read(self, run_id):
        with self.connection() as connection:
            row = connection.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Run not found.")
        return json.loads(row[0])

    def events(self, run_id, after=0, connection=None):
        if connection is None:
            with self.connection() as connection:
                return self.events(run_id, after, connection)
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT document FROM events WHERE run_id=? AND sequence>? ORDER BY sequence", (run_id, after))]

    def save(self, record, connection=None):
        if connection is None:
            with self.transaction() as connection:
                return self.save(record, connection)
        connection.execute("INSERT INTO runs VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET document=excluded.document",
                           (record["id"], encode(record)))

    def append(self, record, event):
        with self.transaction() as connection:
            self.save(record, connection)
            self.insert_events(record["id"], [event], connection)

    def insert_events(self, run_id, events, connection):
        connection.executemany("INSERT INTO events VALUES (?, ?, ?)",
                               [(run_id, event["sequence"], encode(event)) for event in events])

    def catalog(self):
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='presets'").fetchone()
        return json.loads(row[0]) if row else None

    def metadata(self, key, connection=None):
        """Read additive app metadata within an optional caller transaction."""
        if connection is None:
            with self.connection() as connection:
                return self.metadata(key, connection)
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_metadata(self, key, value, connection=None):
        if connection is None:
            with self.transaction() as connection:
                return self.save_metadata(key, value, connection)
        connection.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, encode(value)))

    def save_catalog(self, catalog, connection=None):
        if connection is None:
            with self.transaction() as connection:
                return self.save_catalog(catalog, connection)
        connection.execute("INSERT INTO metadata VALUES ('presets', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (encode(catalog),))

    def listener_config(self):
        with self.connection() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='listener'").fetchone()
        return json.loads(row[0]) if row else None

    def save_listener_config(self, config):
        with self.transaction() as connection:
            connection.execute("INSERT INTO metadata VALUES ('listener', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(config),))

    def ready(self):
        # Exercise writable storage, including locks/full disks; do not scan history.
        with self.transaction() as connection:
            connection.execute("INSERT INTO metadata VALUES ('probe', '1') ON CONFLICT(key) DO UPDATE SET value='1'")
        return True

    def migrate_legacy(self):
        """Import old files once, atomically; leave every source file untouched."""
        from .imports import prepare_run, validate_tree
        from .presets import catalog_from

        with self.transaction() as connection:
            if connection.execute("SELECT 1 FROM metadata WHERE key='legacy_imported'").fetchone():
                return
            for path in sorted(self.directory.glob("*/run.json")):
                run_id = path.parent.name
                if not re.fullmatch(r"[a-f0-9]{32}", run_id):
                    raise ValueError(f"Invalid legacy run directory: {path.parent.name}")
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or record.get("id") != run_id:
                    raise ValueError(f"Legacy run ID mismatch in {path}")
                events, lines = [], []
                event_path = path.parent / "events.jsonl"
                if event_path.exists():
                    lines = event_path.read_text(encoding="utf-8").splitlines()
                for index, line in enumerate(lines):
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        # The old reader ignored a torn final event after a crash.
                        if index != len(lines) - 1:
                            raise ValueError(f"Invalid legacy event in {event_path}") from None
                validate_tree(record)
                validate_tree(events)
                validation_copy = dict(record, events=events)
                if record.get("finished_at") is None and record.get("status") in {"completed", "failed", "cancelled", "interrupted"}:
                    # Old checkpoints could capture a terminal status just before
                    # finally wrote the finish date. Recovery will mark it interrupted.
                    validation_copy["status"] = "running"
                prepare_run(validation_copy, str(path), allow_job=True)  # Validate without changing original evidence.
                existing = connection.execute("SELECT document FROM runs WHERE id=?", (run_id,)).fetchone()
                if existing:
                    if json.loads(existing[0]) != record or self.events(run_id, connection=connection) != events:
                        raise ValueError(f"Conflicting legacy run {run_id}; no files were imported.")
                    continue
                # Event capture can be newer than the old metadata checkpoint.
                record["event_count"] = len(events)
                self.save(record, connection)
                self.insert_events(run_id, events, connection)
            catalog_path = self.directory / "server-presets.json"
            if catalog_path.exists():
                catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                validate_tree(catalog)
                catalog_from(catalog)
                if connection.execute("SELECT 1 FROM metadata WHERE key='presets'").fetchone():
                    raise ValueError("Conflicting saved preset catalog; no files were imported.")
                self.save_catalog(catalog, connection)
            projects_path = self.directory / "projects.json"
            if projects_path.exists():
                from .projects import catalog_from as project_catalog_from
                catalog = json.loads(projects_path.read_text(encoding="utf-8"))
                validate_tree(catalog)
                catalog = project_catalog_from(catalog, self.records(connection))
                if self.metadata("projects", connection) is not None:
                    raise ValueError("Conflicting saved project catalog; no files were imported.")
                self.save_metadata("projects", catalog, connection)
            connection.execute("INSERT INTO metadata VALUES ('legacy_imported', '1')")

    def export_legacy(self, destination):
        """Write a consistent rollback snapshot only into a new directory."""
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=False)
        # One read transaction gives all records/events/presets the same snapshot.
        with self.connection() as connection:
            connection.execute("BEGIN")
            for record in self.records(connection):
                run_id = record["id"]
                if not re.fullmatch(r"[a-f0-9]{32}", run_id):
                    raise ValueError("Invalid stored run ID.")
                path = destination / run_id
                path.mkdir()
                (path / "run.json").write_text(encode(record), encoding="utf-8")
                (path / "events.jsonl").write_text("".join(encode(event) + "\n" for event in self.events(run_id, connection=connection)), encoding="utf-8")
            row = connection.execute("SELECT value FROM metadata WHERE key='presets'").fetchone()
            if row:
                (destination / "server-presets.json").write_text(row[0], encoding="utf-8")
            row = connection.execute("SELECT value FROM metadata WHERE key='projects'").fetchone()
            if row:
                (destination / "projects.json").write_text(row[0], encoding="utf-8")
            connection.commit()
