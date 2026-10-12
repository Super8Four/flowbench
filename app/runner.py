"""Run iPerf3 without a shell and retain a replayable SQLite archive."""

import atexit
import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from .metrics import effective_settings, measurements, series, overview
from .peer import peer_results
from .route import capture_route
from .storage import SQLiteArchive
from .matrix import matrix_plan
from .units import RATE_CONTRACT


TERMINAL = {"completed", "failed", "cancelled", "interrupted", "partial", "skipped"}
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def now():
    return datetime.now(timezone.utc).isoformat()


def host_value(value, field, optional=False):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an IP address or hostname.")
    value = value.strip()
    if optional and not value:
        return ""
    try:
        ipaddress.ip_address(value.split("%")[0])
        if "%" in value and not re.fullmatch(r"[\w.:-]+%[\w.-]+", value):
            raise ValueError()
    except ValueError:
        if len(value) > 253 or not re.fullmatch(
            r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?", value
        ) or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                 for label in value.rstrip(".").split(".")):
            raise ValueError(f"{field} must be an IP address or hostname.") from None
    return value


def settings_from(data, default_mode="upload"):
    if not isinstance(data, dict):
        raise ValueError("Send a JSON object with test settings.")
    result = {}
    for key, default, choices in (
        ("role", "client", {"client", "server"}),
        ("protocol", "tcp", {"tcp", "udp", "both"}),
        ("mode", default_mode, {"upload", "download", "bidir"}),
        ("ip_version", "auto", {"auto", "4", "6"}),
    ):
        value = data.get(key, default)
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"Invalid {key}.")
        result[key] = value
    if result["protocol"] == "both" and result["role"] != "client":
        raise ValueError("Both protocols is a client job. Incoming server tests use the connecting client's protocol.")
    for key, default, low, high in (
        ("port", 5201, 1, 65535), ("duration", 10, 1, 3600),
        ("streams", 1, 1, 32), ("idle_timeout", 300, 1, 3600),
    ):
        value = data.get(key, default)
        if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value)):
            raise ValueError(f"{key} must be a whole number from {low} to {high}.")
        value = int(value)
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}.")
        result[key] = value
    bandwidth = data.get("bandwidth", "10M")
    if not isinstance(bandwidth, str) or not re.fullmatch(r"\d+(?:\.\d+)?[KMG]?", bandwidth, re.I):
        raise ValueError("Bandwidth must look like 10M, 1G, or 0 (unlimited).")
    result["bandwidth"] = bandwidth
    result["target"] = host_value(data.get("target", ""), "Target", result["role"] == "server")
    result["bind"] = host_value(data.get("bind", ""), "Bind address", True)
    for key in ("label", "notes"):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > (200 if key == "label" else 4000):
            raise ValueError(f"{key} is too long or is not text.")
        result[key] = value.strip()
    result["debug"] = data.get("debug", False)
    if not isinstance(result["debug"], bool):
        raise ValueError("debug must be true or false.")
    for key in ("traceroute", "latency", "endpoint_health", "mtu"):
        # Preserve omitted legacy options during import/export normalization.
        if key == "traceroute" or key in data:
            result[key] = data.get(key, False)
            if not isinstance(result[key], bool):
                raise ValueError(f"{key} must be true or false.")
    from .annotations import settings_metadata
    result.update(settings_metadata(data))
    return result


class RunManager:
    def __init__(self, directory, executable=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.storage_lock = (self.directory / ".writer.lock").open("a+b")
        if self.storage_lock.tell() == 0:
            self.storage_lock.write(b"0")
            self.storage_lock.flush()
        self.storage_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.storage_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.storage_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.storage_lock.close()
            raise RuntimeError("Another app process owns this run archive. Use one Waitress process with multiple threads.") from None
        self.lock = threading.RLock()
        self.process = None
        self.active = None
        self.cancel_requested = False
        self.worker = None
        self.record = None
        self.job = None
        self.diagnostics_capture = None
        self.listener = None
        self.events = []
        self.capture_errors = []
        self.storage_error = None
        self.shutdown_requested = False
        self.executable = executable or self.find_executable()
        self.capabilities = self.probe()
        from .flent import capabilities as flent_capabilities
        self.flent_capabilities = flent_capabilities()
        try:
            self.storage = SQLiteArchive(self.directory)
            self.storage.migrate_legacy()
            self.recover()
        except BaseException:
            self.storage_lock.close()
            raise
        atexit.register(self.shutdown)

    @staticmethod
    def find_executable():
        configured = os.getenv("IPERF_PATH")
        if configured:
            return str(Path(configured).expanduser().resolve())
        root = Path(__file__).resolve().parent.parent
        candidates = [root / "bin" / "iperf3.exe", root / "bin" / "iperf3"]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        # The verified Windows archive places its executable in a subdirectory.
        for candidate in sorted((root / "bin").glob("*/iperf3.exe")):
            return str(candidate)
        return shutil.which("iperf3")

    def probe(self):
        info = {"available": False, "version": None, "json_stream": False,
                "full_json": False, "bidir": False, "udp_bidir": False, "path": self.executable}
        if not self.executable:
            info["error"] = "iPerf3 is missing. Install it or set IPERF_PATH, then restart the app."
            return info
        try:
            version = subprocess.run([self.executable, "--version"], capture_output=True,
                                     text=True, timeout=5, creationflags=CREATE_FLAGS)
            help_result = subprocess.run([self.executable, "--help"], capture_output=True,
                                         text=True, timeout=5, creationflags=CREATE_FLAGS)
            help_text = help_result.stdout + help_result.stderr
            if version.returncode != 0 or "iperf 3" not in version.stdout.lower():
                raise ValueError("The configured executable is not a working iPerf3 binary.")
            info.update(available=True, version=version.stdout.splitlines()[0],
                        json_stream="--json-stream" in help_text,
                        full_json="--json-stream-full-output" in help_text,
                        server_max_duration="--server-max-duration" in help_text,
                        bidir="--bidir" in help_text)
            # Verified Windows Cygwin build hangs for UDP bidirectional sessions.
            info["udp_bidir"] = info["bidir"] and os.name != "nt"
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            info["error"] = str(exc)
        return info

    def path(self, run_id):
        if not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise KeyError("Run not found.")
        return self.directory / run_id

    def read(self, run_id):
        with self.lock:
            if self.listener and self.listener.record and run_id == self.listener.record["id"]:
                return copy.deepcopy(self.listener.record)
            if self.job and run_id == self.job["id"]:
                return copy.deepcopy(self.job)
            if self.record and run_id == self.record["id"]:
                return copy.deepcopy(self.record)
            self.path(run_id)  # Validate the public ID before querying storage.
            return self.storage.read(run_id)

    def read_events(self, run_id, after=0):
        with self.lock:
            if self.read(run_id).get("kind") in {"protocol-job", "matrix-job"}:
                return []
            if run_id == self.active:
                return copy.deepcopy(self.events[after:])
            self.path(run_id)
            return self.storage.events(run_id, after)

    def list_runs(self):
        records = []
        with self.lock:
            for saved in self.storage.records():
                try:
                    record = self.read(saved["id"])
                    row = {key: record[key] for key in (
                        "id", "created_at", "finished_at", "status", "settings", "summary", "exit_code"
                    )}
                    row["metrics"] = measurements(record)
                    row["overview"] = overview(record)
                    row["effective_settings"] = effective_settings(record)
                    row["imported"] = record.get("imported")
                    row["kind"] = record.get("kind")
                    row["protocol_job"] = record.get("protocol_job")
                    row["incoming"] = record.get("incoming")
                    row["concurrency"] = record.get("concurrency")
                    for key in ("matrix_job", "diagnostics", "annotations"):
                        row[key] = record.get(key)
                    row["flent"] = ({key: record["flent"].get(key) for key in
                                     ("tool", "version", "netperf_version", "workload", "status", "summaries", "provenance")}
                                    if record.get("flent") else None)
                    row["project_id"] = record["settings"].get("project_id")
                    row["tags"] = record["settings"].get("tags", {})
                    records.append(row)
                except (KeyError, OSError, json.JSONDecodeError):
                    continue
        return sorted(records, key=lambda row: row["created_at"], reverse=True)

    def save(self, record):
        self.path(record["id"])
        self.storage.save(record)

    def recover(self):
        for record in self.storage.records():
            if record["status"] not in TERMINAL or record.get("finished_at") is None:
                record.update(status="interrupted", finished_at=now(),
                              error="The app stopped before this session finished.")
                self.save(record)

    def command(self, settings):
        if settings["protocol"] == "both":
            raise ValueError("Both protocols must run as separate TCP and UDP tests.")
        cmd = [self.executable]
        if settings["role"] == "server":
            cmd += ["-s", "-1", "--idle-timeout", str(settings["idle_timeout"])]
        else:
            cmd += ["-c", settings["target"], "-t", str(settings["duration"]),
                    "-P", str(settings["streams"]), "--connect-timeout", "5000", "--get-server-output"]
            if settings["protocol"] == "udp":
                if settings["mode"] == "bidir" and not self.capabilities["udp_bidir"]:
                    raise ValueError("Bidirectional UDP is unavailable on this iPerf3 build. Use UDP upload/download or TCP bidirectional; Linux supports UDP bidirectional.")
                cmd += ["-u", "-b", settings["bandwidth"]]
            if settings["mode"] == "download":
                cmd.append("-R")
            elif settings["mode"] == "bidir":
                if not self.capabilities["bidir"]:
                    raise ValueError("This iPerf3 version does not support bidirectional tests.")
                cmd.append("--bidir")
        cmd += ["-p", str(settings["port"]), "-i", "1", "--forceflush"]
        if settings.get("ip_version", "auto") != "auto":
            cmd.append("-" + settings["ip_version"])
        if settings["bind"]:
            cmd += ["-B", settings["bind"]]
        if self.capabilities["json_stream"]:
            cmd.append("--json-stream")
            if self.capabilities["full_json"]:
                cmd.append("--json-stream-full-output")
        else:
            cmd.append("--verbose")
        if settings["debug"]:
            cmd.append("--debug")
        return cmd

    def start(self, data):
        settings = settings_from(data, default_mode="bidir")
        from .projects import validate_project_assignment
        validate_project_assignment(self, settings)
        if self.storage_error:
            raise RuntimeError("Archive writes failed. Restore storage access and restart the app: " + self.storage_error)
        if not self.capabilities["available"]:
            raise RuntimeError(self.capabilities["error"])
        protocols = ["tcp", "udp"] if settings["protocol"] == "both" else [settings["protocol"]]
        commands = [self.command(dict(settings, protocol=protocol)) for protocol in protocols]
        with self.lock:
            self.ensure_running()
            if self.active:
                raise RuntimeError("A session is already running. Stop it before starting another.")
            if settings["role"] == "server" and self.listener and self.listener.running and settings["port"] == self.listener.config["port"]:
                raise RuntimeError("The managed listener already owns this port; incoming tests are captured automatically.")
            record = {"schema_version": 1, "id": uuid4().hex, "created_at": now(),
                      "finished_at": None, "status": "starting", "settings": settings,
                      "command": commands[0], "iperf_version": self.capabilities["version"],
                      "output_format": "json-stream" if self.capabilities["json_stream"] else "text",
                      "exit_code": None, "error": None, "summary": {}, "intervals": [],
                      "native_result": None, "test_parameters": None, "event_count": 0}
            self.job = None
            if settings["protocol"] == "both":
                record.update(kind="protocol-job", command=[], output_format="protocol-job", children=[])
                children = []
                for index, protocol in enumerate(protocols):
                    child = copy.deepcopy(record)
                    child.pop("kind")
                    child.pop("children")
                    child.update(id=uuid4().hex, status="queued", settings=dict(settings, protocol=protocol),
                                 command=commands[index], output_format="json-stream" if self.capabilities["json_stream"] else "text",
                                 protocol_job={"id": record["id"], "protocol": protocol, "order": index + 1})
                    record["children"].append({"id": child["id"], "protocol": protocol})
                    children.append(child)
                with self.storage.transaction() as connection:
                    for row in [record, *children]:
                        self.storage.save(row, connection)
                self.job = record
            else:
                self.save(record)
            self.active, self.record = record["id"], record
            self.events, self.cancel_requested = [], False
            self.capture_errors = []
            self.worker = threading.Thread(target=self.run_job if self.job else self.run, name=f"iperf-{self.active}", daemon=True)
            self.worker.start()
            return copy.deepcopy(record)

    def start_matrix(self, data):
        settings, combinations, plan = matrix_plan(data, settings_from)
        from .projects import validate_project_assignment
        validate_project_assignment(self, settings)
        if self.storage_error:
            raise RuntimeError("Archive writes failed. Restore storage access and restart the app: " + self.storage_error)
        if not self.capabilities["available"]:
            raise RuntimeError(self.capabilities["error"])
        commands = [self.command(child) for child in combinations]
        with self.lock:
            self.ensure_running()
            if self.active:
                raise RuntimeError("A session is already running. Stop it before starting another.")
            record = {"schema_version": 1, "id": uuid4().hex, "created_at": now(),
                      "finished_at": None, "status": "starting", "settings": settings,
                      "command": [], "iperf_version": self.capabilities["version"],
                      "output_format": "matrix-job", "kind": "matrix-job", "matrix": plan,
                      "exit_code": None, "error": None, "summary": {}, "intervals": [],
                      "native_result": None, "test_parameters": None, "event_count": 0, "children": []}
            children = []
            for index, (child_settings, command) in enumerate(zip(combinations, commands), 1):
                child = copy.deepcopy(record)
                for key in ("kind", "children", "matrix"):
                    child.pop(key)
                child.update(id=uuid4().hex, status="queued", settings=child_settings, command=command,
                             output_format="json-stream" if self.capabilities["json_stream"] else "text",
                             matrix_job={"id": record["id"], "order": index})
                record["children"].append({"id": child["id"], "protocol": child_settings["protocol"],
                                           "mode": child_settings["mode"], "streams": child_settings["streams"], "order": index})
                children.append(child)
            with self.storage.transaction() as connection:
                for row in [record, *children]:
                    self.storage.save(row, connection)
            self.job = self.record = record
            self.active = record["id"]
            self.events, self.cancel_requested, self.capture_errors = [], False, []
            self.worker = threading.Thread(target=self.run_job, name=f"matrix-{self.active}", daemon=True)
            self.worker.start()
            return copy.deepcopy(record)

    @property
    def active_id(self):
        return self.job["id"] if self.active and self.job else self.active

    def protocol_runs(self, job):
        with self.lock:
            result = []
            for reference in job["children"]:
                child = self.read(reference["id"])
                result.append({"id": child["id"], "protocol": child["settings"]["protocol"],
                               "mode": effective_settings(child)["mode"], "status": child["status"],
                               "streams": effective_settings(child)["streams"], "settings": child["settings"],
                               "diagnostics": child.get("diagnostics"), "annotations": child.get("annotations"),
                               "error": child.get("error"), "metrics": measurements(child),
                               "created_at": child["created_at"], "finished_at": child["finished_at"]})
            return result

    job_runs = protocol_runs

    def run_job(self):
        try:
            with self.lock:
                self.job["status"] = "running"
                self.save(self.job)
            for reference in self.job["children"]:
                with self.lock:
                    child = self.storage.read(reference["id"])
                    if self.cancel_requested or self.storage_error:
                        child.update(status="skipped", finished_at=now(),
                                     error="Job stopped before this test started.")
                        self.save(child)
                        continue
                    # Keep archive ownership and the active job reserved between tests.
                    child.update(created_at=now(), status="starting")
                    self.save(child)
                    self.record, self.active = child, child["id"]
                    self.events, self.capture_errors = [], []
                self.run(grouped=True)
            with self.lock:
                statuses = [row["status"] for row in self.protocol_runs(self.job)]
                self.job["status"] = ("cancelled" if self.cancel_requested else
                                      "completed" if all(status == "completed" for status in statuses) and not self.storage_error else
                                      "partial" if "completed" in statuses else "failed")
                if self.job["status"] == "partial":
                    self.job["error"] = "Some tests did not complete; inspect each test's retained evidence."
                elif self.job["status"] == "failed":
                    self.job["error"] = self.storage_error or "No test completed successfully."
        except Exception as exc:
            with self.lock:
                self.job.update(status="failed", error=str(exc))
                for reference in self.job["children"]:
                    try:
                        child = self.read(reference["id"])
                        if child["status"] == "queued":
                            child.update(status="skipped", finished_at=now(), error="Job failed before this test started.")
                            self.save(child)
                    except OSError:
                        pass
                try:
                    if any(row["status"] == "completed" for row in self.protocol_runs(self.job)):
                        self.job["status"] = "partial"
                except OSError:
                    pass
        finally:
            with self.lock:
                self.job["finished_at"] = now()
                try:
                    self.save(self.job)
                except OSError as exc:
                    self.storage_error = str(exc)
                    self.job.update(status="failed", error="Archive write failed: " + str(exc))
                self.active = None

    def log(self, source, message):
        with self.lock:
            event = {"sequence": len(self.events) + 1, "timestamp": now(),
                     "source": source, "message": message}
            previous = copy.deepcopy(self.record)
            try:
                self.record["event_count"] = event["sequence"]
                if source == "stdout" and self.record.get("kind") != "flent-run":
                    self.parse(message)
                # Commit the event and corresponding measured state together,
                # before either is published to HTTP readers.
                self.storage.append(self.record, event)
            except OSError as exc:
                self.record = previous
                self.storage_error = str(exc)
                raise
            self.events.append(event)

    def parse(self, line):
        try:
            obj = json.loads(line, parse_constant=lambda value: None)
        except json.JSONDecodeError:
            self.parse_text(line)
            return
        if not isinstance(obj, dict):
            return
        event, data = obj.get("event"), obj.get("data")
        if event == "start" and isinstance(data, dict):
            self.record["test_parameters"] = data.get("test_start")
        elif event == "interval" and isinstance(data, dict):
            self.record["intervals"].append(data)
        elif event == "error":
            self.record["error"] = data if isinstance(data, str) else json.dumps(data)
        elif event == "server_output_text" and isinstance(data, str):
            self.record["server_output_text"] = data
        elif event == "server_output_json" and isinstance(data, (str, dict)):
            self.record["server_output_json"] = data
        elif event == "end" and isinstance(data, dict):
            self.record["native_result"] = obj
            self.record["summary"] = data.get("end", data)
        elif "end" in obj:
            self.record["native_result"] = obj
            self.record["summary"] = obj["end"]
        if "error" in obj:
            self.record["error"] = str(obj["error"])

    def parse_text(self, line):
        # Compatibility with older iPerf3 builds; the complete raw log is also retained.
        match = re.search(r"\[\s*(?P<stream>\d+|SUM)\s*\].*?(?P<start>\d+\.\d+)-\s*"
                          r"(?P<end>\d+\.\d+)\s+sec.*?(?P<rate>\d+(?:\.\d+)?)\s+"
                          r"(?P<unit>[KMGT]?)bits/sec", line)
        if match:
            entry = {"start": float(match["start"]), "end": float(match["end"]),
                     "bits_per_second": float(match["rate"]) * 1000 ** (" KMGT".index(match["unit"]) if match["unit"] else 0),
                     "stream": match["stream"]}
            direction = "download" if "[RX-C]" in line else "upload" if "[TX-C]" in line else None
            if direction:
                entry["direction"] = direction
            if "sender" in line or "receiver" in line:
                role = "sender" if "sender" in line else "receiver"
                key = (direction + "_" if direction else "") + role
                self.record["summary"].setdefault("text_summaries", {}).setdefault(key, []).append(entry)
            else:
                self.record["intervals"].append(entry)
        if "iperf3: error" in line.lower():
            self.record["error"] = line

    def consume(self, stream, source):
        full_output = []
        try:
            for line in iter(stream.readline, ""):
                line = line.rstrip("\r\n")
                self.log(source, line)
                if source == "stdout" and (full_output or line.strip() == "{"):
                    full_output.append(line)
            if full_output:
                try:
                    obj = json.loads("\n".join(full_output), parse_constant=lambda value: None)
                    with self.lock:
                        self.record["native_result"] = obj
                        self.record["summary"] = obj.get("end", self.record["summary"])
                        self.record["test_parameters"] = obj.get("start", {}).get("test_start", self.record["test_parameters"])
                        if "error" in obj:
                            self.record["error"] = str(obj["error"])
                        self.save(self.record)
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            with self.lock:
                self.capture_errors.append(f"{source}: {exc}")
                if self.process and self.process.poll() is None:
                    self.process.kill()
        finally:
            stream.close()

    def run(self, grouped=False):
        readers = []
        diagnostics = None
        try:
            settings = self.record["settings"]
            if settings["role"] == "client" and any(settings.get(key, False) for key in ("latency", "endpoint_health", "mtu")):
                from .diagnostics import DiagnosticsCapture
                diagnostics = DiagnosticsCapture(settings, lambda: self.cancel_requested)
                with self.lock:
                    self.diagnostics_capture = diagnostics
                diagnostic_owner, diagnostic_id = diagnostics, self.record["id"]
                def diagnostic_checkpoint(evidence):
                    with self.lock:
                        if self.record["id"] != diagnostic_id or self.diagnostics_capture is not diagnostic_owner:
                            return
                        previous = copy.deepcopy(self.record)
                        self.record["diagnostics"] = evidence
                        try:
                            self.save(self.record)
                        except OSError as exc:
                            self.record = previous
                            self.storage_error = str(exc)
                            self.record["error"] = "Diagnostic checkpoint archive write failed: " + str(exc)
                            self.capture_errors.append(self.record["error"])
                            if self.process and self.process.poll() is None:
                                self.process.kill()
                            raise
                diagnostics.on_update = diagnostic_checkpoint
                # Baseline probes must finish (or cancel) before traffic starts.
                try:
                    diagnostics.baseline()
                except Exception as diagnostic_error:
                    with self.lock:
                        self.record["diagnostics"] = diagnostics.failure(diagnostic_error)
                    diagnostics = None
                if diagnostics:
                    # Persist the measured baseline before starting traffic. Loaded
                    # samples checkpoint once per probe, preserving restart evidence.
                    diagnostic_checkpoint(diagnostics.snapshot())
            self.log("system", "Starting: " + subprocess.list2cmdline(self.record["command"]))
            with self.lock:
                if not self.cancel_requested:
                    self.process = subprocess.Popen(self.record["command"], stdout=subprocess.PIPE,
                                                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                                    errors="replace", bufsize=1, creationflags=CREATE_FLAGS)
                    self.record["status"] = "running"
                    if self.listener:
                        self.listener.mark_overlap(self.record)
                    self.save(self.record)
                else:
                    self.record["status"] = "cancelled"
                    return
            if diagnostics:
                try:
                    diagnostics.start()
                except Exception as diagnostic_error:
                    with self.lock:
                        self.record["diagnostics"] = diagnostics.failure(diagnostic_error)
                    diagnostics = None
            for stream, source in ((self.process.stdout, "stdout"), (self.process.stderr, "stderr")):
                reader = threading.Thread(target=self.consume, args=(stream, source), daemon=True)
                readers.append(reader)
                reader.start()
            settings = self.record["settings"]
            # Bound stalled sessions, including one-off servers that have accepted a connection.
            timeout = settings["duration"] + 45 if settings["role"] == "client" else settings["idle_timeout"] + 3645
            try:
                code = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.log("system", "Session exceeded its time limit; terminating iPerf3.")
                self.process.kill()
                code = self.process.wait()
                self.record["error"] = "Session exceeded its time limit."
            for reader in readers:
                reader.join()
            if diagnostics:
                try:
                    result = diagnostics.finish()
                except Exception as diagnostic_error:
                    result = diagnostics.failure(diagnostic_error)
                with self.lock:
                    self.record["diagnostics"] = result
                diagnostics = None
            with self.lock:
                self.record["exit_code"] = code
                if self.capture_errors:
                    self.record["error"] = "Output capture failed: " + "; ".join(self.capture_errors)
                if self.cancel_requested:
                    outcome = "cancelled"
                else:
                    outcome = "completed" if code == 0 and not self.record["error"] else "failed"
                trace = settings["role"] == "client" and settings["traceroute"] and outcome == "completed"
                self.record["status"] = "running" if trace else outcome
                self.log("system", f"iPerf3 exited with code {code}; throughput {outcome}.")
                self.record["route"] = {"status": "pending" if trace else "skipped", "hops": [],
                                        "note": "Route capture follows a successful client test when selected."}
                if trace:
                    self.record["status"] = "running"
                    self.log("system", "Throughput finished; capturing route (maximum 20 seconds, 16 hops).")
            if trace:
                def begin_route(command):
                    with self.lock:
                        if self.cancel_requested:
                            return None
                        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                                        text=True, errors="replace", creationflags=CREATE_FLAGS)
                        return self.process
                result = capture_route(settings, begin_route, lambda: self.cancel_requested)
                with self.lock:
                    self.record["route"] = result
                    self.record["status"] = "cancelled" if self.cancel_requested else outcome
                    self.log("system", "Route capture: " + result["status"] + ".")
        except Exception as exc:
            with self.lock:
                if self.process and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
                if self.process and not readers:
                    # Storage may fail immediately after Popen, before readers own the pipes.
                    self.process.stdout.close()
                    self.process.stderr.close()
                self.record.update(status="failed", error=str(exc))
                try:
                    self.log("system", "Session failed: " + str(exc))
                except OSError as log_error:
                    self.storage_error = str(log_error)
        finally:
            if diagnostics:
                try:
                    result = diagnostics.finish()
                    with self.lock:
                        self.record["diagnostics"] = result
                except Exception as diagnostic_error:
                    with self.lock:
                        self.record["diagnostics"] = diagnostics.failure(diagnostic_error)
            with self.lock:
                self.diagnostics_capture = None
                self.record["finished_at"] = now()
                try:
                    self.save(self.record)
                except OSError as save_error:
                    self.storage_error = str(save_error)
                    self.record.update(status="failed", error="Archive write failed: " + str(save_error))
                finally:
                    self.process = None
                    if not grouped:
                        self.active = None

    def stop(self, run_id):
        with self.lock:
            if self.listener and self.listener.record and run_id == self.listener.record["id"]:
                raise ValueError("Use Stop listener to stop an incoming test and its managed listener.")
            if self.active and self.job and run_id in {self.job["id"], *(row["id"] for row in self.job["children"])}:
                self.cancel_requested = True
                try:
                    self.job["cancel_requested"] = True
                    self.save(self.job)
                    if self.record and self.record.get("kind") not in {"protocol-job", "matrix-job"} and self.record["status"] not in TERMINAL:
                        self.log("system", "Grouped job stopped by user.")
                finally:
                    if self.diagnostics_capture:
                        self.diagnostics_capture.cancel()
                    if self.process and self.process.poll() is None:
                        self.process.terminate()
                return
            if run_id != self.active:
                self.read(run_id)
                return
            self.cancel_requested = True
            try:
                self.log("system", "Stop requested by user.")
            finally:
                if self.diagnostics_capture:
                    self.diagnostics_capture.cancel()
                if self.process and self.process.poll() is None:
                    if self.record.get("kind") == "flent-run":
                        from .flent import stop_process
                        stop_process(self.process)
                    else:
                        self.process.terminate()

    def shutdown(self):
        with self.lock:
            self.shutdown_requested = True
        if self.listener:
            try:
                self.listener.stop(disable=False)
            except OSError as exc:
                self.storage_error = str(exc)
        if self.active:
            try:
                self.stop(self.active)
            except OSError as exc:
                self.storage_error = str(exc)
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=15)
        if self.worker and self.worker.is_alive():
            # A writer still holds the archive. Preserve ownership and honest
            # unavailable readiness instead of letting a second app race it.
            self.storage_error = "Shutdown did not finish within 15 seconds; archive writer ownership is retained until the worker exits."
            return
        if not self.storage_lock.closed:
            self.storage_lock.close()
        atexit.unregister(self.shutdown)

    def ensure_running(self):
        if self.shutdown_requested or self.storage_lock.closed:
            raise RuntimeError("The app is shutting down or the archive writer is closed. Restart the app before starting traffic.")

    def annotate(self, run_id, data):
        from .annotations import annotate_run
        return annotate_run(self, run_id, data)

    def start_flent(self, data):
        from .flent import settings as flent_settings
        from .projects import validate_project_assignment
        settings = flent_settings(data)
        validate_project_assignment(self, settings)
        if self.storage_error:
            raise RuntimeError("Archive writes failed. Restore storage access and restart the app: " + self.storage_error)
        if not self.flent_capabilities["available"]:
            raise RuntimeError(self.flent_capabilities.get("error", "Optional Flent tools are unavailable."))
        with self.lock:
            self.ensure_running()
            if self.active:
                raise RuntimeError("A session is already running. Stop it before starting another.")
            record = {"schema_version": 1, "id": uuid4().hex, "created_at": now(), "finished_at": None,
                      "status": "starting", "settings": settings, "kind": "flent-run", "command": [],
                      "iperf_version": None, "output_format": "flent-native", "exit_code": None, "error": None,
                      "summary": {}, "intervals": [], "native_result": None, "test_parameters": None, "event_count": 0,
                      "flent": {"tool": "Flent", "version": self.flent_capabilities["version"], "workload": "rrul",
                                "netperf_version": self.flent_capabilities.get("netperf_version"),
                                "status": "missing", "data": None, "series": [], "summaries": [],
                                "provenance": "External Flent CLI; native result pending."}}
            self.save(record)
            self.active, self.record, self.job = record["id"], record, None
            self.events, self.cancel_requested, self.capture_errors = [], False, []
            self.worker = threading.Thread(target=self.run_flent, name=f"flent-{self.active}", daemon=True)
            self.worker.start()
            return copy.deepcopy(record)

    def run_flent(self):
        from .flent import command, read_result, stop_process
        readers = []
        process = None
        try:
            # Only files created in this owned temporary child directory are cleaned.
            with tempfile.TemporaryDirectory(prefix="flent-", dir=self.directory) as directory:
                with self.lock:
                    if self.cancel_requested:
                        self.record["status"] = "cancelled"
                        self.record["flent"]["status"] = "cancelled"
                        return
                    self.record["command"] = command(self.flent_capabilities, self.record["settings"], directory)
                    self.log("system", "Starting actual Flent RRUL: " + subprocess.list2cmdline(self.record["command"]))
                    process = self.process = subprocess.Popen(self.record["command"], stdout=subprocess.PIPE,
                                                             stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                                             errors="replace", cwd=directory, start_new_session=True)
                    self.record["status"] = "running"
                    if self.listener:
                        self.listener.mark_overlap(self.record)
                    self.save(self.record)
                for stream, source in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    thread = threading.Thread(target=self.consume, args=(stream, source), daemon=True)
                    thread.start()
                    readers.append(thread)
                deadline = time.monotonic() + self.record["settings"]["duration"] + 45
                cancel_deadline = None
                while True:
                    try:
                        code = process.wait(timeout=.2)
                        break
                    except subprocess.TimeoutExpired:
                        if self.cancel_requested and cancel_deadline is None:
                            stop_process(process)
                            cancel_deadline = time.monotonic() + 2
                        if time.monotonic() >= (cancel_deadline or deadline):
                            stop_process(process, force=True)
                            code = process.wait(timeout=5)
                            if not self.cancel_requested:
                                self.record["error"] = "Flent exceeded its bounded workload time limit."
                            break
                # Kill any leftover descendants in the owned new session, including
                # after a CLI crash; never touch the independently owned Netserver.
                stop_process(process, force=True)
                for reader in readers:
                    reader.join(timeout=5)
                with self.lock:
                    self.record["exit_code"] = code
                    if code != 0 and not self.cancel_requested:
                        self.record["error"] = self.record["error"] or f"Flent exited with code {code}; inspect the captured logs and partial native evidence."
                    try:
                        evidence = read_result(directory, self.flent_capabilities["version"])
                        evidence["netperf_version"] = self.flent_capabilities.get("netperf_version")
                        if self.cancel_requested or code != 0:
                            evidence["status"] = "partial"
                        self.record["flent"] = evidence
                        if not self.cancel_requested and code == 0 and not evidence["quality"]["complete"]:
                            self.record["error"] = "Flent returned incomplete RRUL measurements; inspect missing TCP/RTT series and retained native evidence."
                    except (OSError, ValueError) as exc:
                        self.record["flent"].update(status="cancelled" if self.cancel_requested else "missing", error=str(exc))
                        if not self.cancel_requested:
                            self.record["error"] = self.record["error"] or str(exc)
                    if self.capture_errors:
                        self.record["error"] = "Output capture failed: " + "; ".join(self.capture_errors)
                    self.record["status"] = ("cancelled" if self.cancel_requested else
                                             "partial" if code == 0 and self.record["flent"].get("status") == "partial" and not self.capture_errors else
                                             "completed" if code == 0 and not self.record["error"] else "failed")
                    self.log("system", "Actual Flent workload " + self.record["status"] + "; native evidence " + self.record["flent"]["status"] + ".")
        except Exception as exc:
            with self.lock:
                self.record.update(status="cancelled" if self.cancel_requested else "failed", error=str(exc))
                self.record["flent"].update(status="cancelled" if self.cancel_requested else "failed", error=str(exc))
        finally:
            if process:
                stop_process(process, force=True)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                if not readers:
                    process.stdout.close()
                    process.stderr.close()
            for reader in readers:
                reader.join(timeout=5)
            with self.lock:
                self.record["finished_at"] = now()
                try:
                    self.save(self.record)
                except OSError as exc:
                    self.storage_error = str(exc)
                    self.record.update(status="failed", error="Archive write failed: " + str(exc))
                self.process, self.active = None, None

    def export(self, run_id):
        with self.lock:
            record = self.read(run_id)
            record["rate_units"] = RATE_CONTRACT
            if record.get("kind") in {"protocol-job", "matrix-job"}:
                record["runs"] = [self.export(child["id"]) for child in record["children"]]
                return record
            record["metrics"] = measurements(record)
            record["effective_settings"] = effective_settings(record)
            record["series"] = series(record)
            record["overview"] = overview(record)
            record["peer"] = peer_results(record)
            events = self.read_events(run_id)
            record["events"] = events
            record["raw_stdout"] = "\n".join(row["message"] for row in events if row["source"] == "stdout")
            record["raw_stderr"] = "\n".join(row["message"] for row in events if row["source"] == "stderr")
            return record
