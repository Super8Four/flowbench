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
from .storage import SQLiteArchive
from .matrix import matrix_plan
from .units import RATE_CONTRACT
from .endpoints import (initial_endpoints, observe_iperf_start, derive_endpoints,
                        endpoint_view, endpoints_summary, validate_context, runtime_hostname)


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
        self.preflight_capture = None
        self.check_capture = None
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
                    if row["overview"].get("issues"):
                        row["measurement_warnings"] = row["overview"]["issues"]
                    row["effective_settings"] = effective_settings(record)
                    row["imported"] = record.get("imported")
                    row["kind"] = record.get("kind")
                    row["protocol_job"] = record.get("protocol_job")
                    row["incoming"] = record.get("incoming")
                    row["concurrency"] = record.get("concurrency")
                    for key in ("matrix_job", "diagnostics", "annotations", "check_type", "lineage", "started_at", "time_quality"):
                        row[key] = copy.deepcopy(record.get(key))
                    if record.get("kind") == "diagnostic-check":
                        from .metrics import check_comparison_eligible
                        result = record.get("result")
                        row["result"] = ({key: copy.deepcopy(value) for key, value in result.items() if key != "attempts"}
                                         if isinstance(result, dict) else None)
                        row["check_comparable"] = check_comparison_eligible(record)
                    row["flent"] = ({key: record["flent"].get(key) for key in
                                     ("tool", "version", "netperf_version", "workload", "status", "summaries", "provenance")}
                                    if record.get("flent") else None)
                    row["project_id"] = record["settings"].get("project_id")
                    row["tags"] = record["settings"].get("tags", {})
                    row["endpoints_summary"] = endpoints_summary(record)
                    records.append(row)
                except (KeyError, OSError, json.JSONDecodeError):
                    continue
        return sorted(records, key=lambda row: row["created_at"], reverse=True)

    def save(self, record):
        self.path(record["id"])
        self.storage.save(record)

    def recover(self):
        for record in self.storage.records():
            changed = False
            if record["status"] not in TERMINAL or record.get("finished_at") is None:
                record.update(status="interrupted", finished_at=now(),
                              error="The app stopped before this session finished.")
                changed = True
            if changed and record.get("kind") == "diagnostic-check":
                result = record.get("result")
                if isinstance(result, dict):
                    result.update(status="interrupted", finished_at=record["finished_at"],
                                  error="The app stopped before this check finished. Retained attempts were not replayed.")
                    if result.get("time_quality", {}).get("status") == "pending":
                        from .time_quality import time_quality_result
                        result["time_quality"] = time_quality_result("unavailable", note="The app stopped before a clock observation was retained.")
                    record["time_quality"] = copy.deepcopy(result.get("time_quality"))
            preflight = record.get("preflight")
            if preflight and (preflight.get("status") == "pending" or preflight.get("time_quality", {}).get("status") == "pending"):
                from .time_quality import time_quality_result
                preflight["status"] = "partial" if preflight.get("phase") == "before_traffic" else "not_applicable"
                if preflight.get("time_quality", {}).get("status") == "pending":
                    preflight["time_quality"] = time_quality_result(status="unavailable", note="The app stopped before the local time query was retained. No clock result has been inferred.")
                if record.get("route", {}).get("status") == "pending":
                    record["route"].update(status="unavailable", note="The app stopped before route capture was retained. No missing hops have been inferred.")
                changed = True
            if changed:
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

    def start(self, data, *, initiator_context=None):
        return self._admit("iperf-run", data, initiator_context=initiator_context)["run"]

    def start_matrix(self, data, *, initiator_context=None):
        return self._admit("matrix-job", data, initiator_context=initiator_context)["run"]

    def start_check(self, data, *, initiator_context=None):
        return self._admit("diagnostic-check", data, initiator_context=initiator_context)["run"]

    def _request_replay(self, token, intent):
        from .reruns import AdmissionConflict, AdmissionUnavailable
        saved = self.storage.execution_request(token) if token else None
        if saved is None:
            return None
        if not isinstance(saved, dict) or not isinstance(saved.get("result_id"), str):
            raise AdmissionUnavailable("The retained execution request is malformed; restore archive metadata before retrying.")
        if saved.get("intent_sha256") != intent:
            raise AdmissionConflict("This request_id already belongs to a different execution intent.")
        try:
            return {"run": self.read(saved["result_id"]), "replayed": True}
        except (KeyError, TypeError):
            raise AdmissionUnavailable("The retained execution request has no readable result; restore the archive before retrying.") from None

    def _prepare_execution(self, kind, data):
        """Pure configuration/command preparation. No subprocess or network work."""
        from .reruns import AdmissionUnavailable
        from .projects import validate_project_assignment
        combinations, plan = None, None
        if kind == "diagnostic-check":
            from .checks import prepare_check, initial_result
            check_type, settings = prepare_check(data)
        elif kind == "flent-run":
            from .flent import settings as flent_settings
            settings = flent_settings(data)
        elif kind == "matrix-job":
            settings, combinations, plan = matrix_plan(data, settings_from)
        else:
            settings = settings_from(data, default_mode="bidir")
            if settings["protocol"] == "both":
                kind = "protocol-job"
                combinations = [dict(settings, protocol=protocol) for protocol in ("tcp", "udp")]
        validate_project_assignment(self, settings)
        if kind == "flent-run":
            if not self.flent_capabilities["available"]:
                raise AdmissionUnavailable(self.flent_capabilities.get("error", "Optional Flent tools are unavailable."))
        elif kind != "diagnostic-check" and not self.capabilities["available"]:
            raise AdmissionUnavailable(self.capabilities["error"])
        if settings.get("role") == "server" and self.listener and self.listener.running and settings["port"] == self.listener.config["port"]:
            from .reruns import AdmissionConflict
            raise AdmissionConflict("The managed listener already owns this port; incoming tests are captured automatically.")
        record = {"schema_version": 1, "id": uuid4().hex, "created_at": now(), "started_at": None,
                  "finished_at": None, "status": "starting", "settings": settings,
                  "command": [], "iperf_version": None, "output_format": kind,
                  "exit_code": None, "error": None, "summary": {}, "intervals": [],
                  "native_result": None, "test_parameters": None, "event_count": 0}
        children = []
        if kind == "diagnostic-check":
            record.update(kind=kind, status="pending", check_type=check_type, result=initial_result(check_type, settings), raw_stdout="")
            worker = self.run_check
        elif kind == "flent-run":
            record.update(kind=kind, output_format="flent-native", flent={
                "tool": "Flent", "version": self.flent_capabilities["version"], "workload": "rrul",
                "netperf_version": self.flent_capabilities.get("netperf_version"), "status": "missing",
                "data": None, "series": [], "summaries": [], "provenance": "External Flent CLI; native result pending."})
            worker = self.run_flent
        else:
            record.update(iperf_version=self.capabilities["version"],
                          output_format="json-stream" if self.capabilities["json_stream"] else "text")
            if combinations is None:
                record["command"] = self.command(settings)
                worker = self.run
            else:
                record.update(kind=kind, children=[], output_format=kind)
                if plan is not None:
                    record["matrix"] = plan
                for index, child_settings in enumerate(combinations, 1):
                    child = copy.deepcopy(record)
                    for key in ("kind", "children", "matrix"):
                        child.pop(key, None)
                    membership = {"id": record["id"], "order": index}
                    if kind == "protocol-job":
                        membership["protocol"] = child_settings["protocol"]
                    child.update(id=uuid4().hex, status="queued", settings=child_settings,
                                 command=self.command(child_settings),
                                 output_format="json-stream" if self.capabilities["json_stream"] else "text")
                    child["matrix_job" if kind == "matrix-job" else "protocol_job"] = membership
                    reference = {"id": child["id"], "protocol": child_settings["protocol"]}
                    if kind == "matrix-job":
                        reference.update(mode=child_settings["mode"], streams=child_settings["streams"], order=index)
                    record["children"].append(reference)
                    children.append(child)
                worker = self.run_job
        return record, children, worker

    def _admit(self, kind, data, source_id=None, *, initiator_context=None):
        from .reruns import request_identity, lineage_for, AdmissionConflict, AdmissionUnavailable
        token, intent = request_identity(data, "rerun:" + source_id if source_id else kind)
        with self.lock:
            replay = self._request_replay(token, intent)
            if replay is not None:
                return replay
            if "initiator_context" in data or isinstance(data.get("settings"), dict) and "initiator_context" in data["settings"]:
                raise ValueError("Initiating HTTP context must not be supplied in execution JSON.")
            if initiator_context is not None:
                validate_context(initiator_context)
            source = None
            snapshot = None
            config = {key: value for key, value in data.items() if key != "request_id"}
            if source_id:
                if token is None:
                    raise ValueError("Run again requires a request_id for safe transport retries.")
                allowed = {"request_id", "source_fingerprint", "confirm_netserver", "confirm_wait_for_client"}
                if set(data) - allowed:
                    raise ValueError("Run again accepts only its request ID, source fingerprint and required confirmations.")
                for key in ("confirm_netserver", "confirm_wait_for_client"):
                    if key in data and not isinstance(data[key], bool):
                        raise ValueError(key + " must be true or false.")
                expected = data.get("source_fingerprint")
                if "source_fingerprint" in data and (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
                    raise ValueError("source_fingerprint must be a SHA-256 digest.")
                plan = self.rerun_plan(source_id)
                source = self.read(source_id)
                snapshot = plan["source_fingerprint"]
                if data.get("source_fingerprint", snapshot) != snapshot:
                    raise AdmissionConflict("Saved configuration changed. Reload the Run again plan before starting.")
                for confirmation in plan["required_confirmations"]:
                    if data.get(confirmation) is not True:
                        raise ValueError("Fresh confirmation required: " + confirmation)
                kind, config = plan["kind"], dict(plan["config"])
                config.update({key: data[key] for key in plan["required_confirmations"]})
            self.ensure_running()
            if self.storage_error:
                raise AdmissionUnavailable("Restore archive storage access and restart the app: " + self.storage_error)
            if self.active:
                raise AdmissionConflict("A session is already running. Stop it before starting another.")
            record, children, worker = self._prepare_execution(kind, config)
            hostname = runtime_hostname()
            for row in [record, *children]:
                row["endpoints"] = initial_endpoints(row, hostname)
                if initiator_context is not None:
                    row["initiator_context"] = copy.deepcopy(initiator_context)
            if source_id:
                record["lineage"] = lineage_for(source_id)
                originals = source.get("children", [])
                for child, original in zip(children, originals):
                    child["lineage"] = lineage_for(original["id"])
            with self.storage.transaction() as connection:
                for row in [record, *children]:
                    self.storage.create(row, connection)
                if token:
                    self.storage.claim_execution_request(token, {
                        "schema_version": 1, "intent_sha256": intent, "source_snapshot_sha256": snapshot,
                        "result_id": record["id"], "created_at": record["created_at"]}, connection)
            self.job = record if children else None
            self.active, self.record = record["id"], record
            self.events, self.cancel_requested, self.capture_errors = [], False, []
            try:
                self.worker = threading.Thread(target=worker, name=f"execution-{self.active}", daemon=True)
                self.worker.start()
            except Exception as exc:
                record.update(status="failed", finished_at=now(), error="Worker launch failed: " + str(exc))
                if record.get("kind") == "diagnostic-check":
                    record["result"].update(status="error", finished_at=record["finished_at"], error=record["error"])
                for child in children:
                    child.update(status="skipped", finished_at=record["finished_at"], error="Worker failed before this test started.")
                self.active = None
                self.worker = None
                with self.storage.transaction() as connection:
                    for row in [record, *children]:
                        self.storage.save(row, connection)
                raise AdmissionUnavailable(record["error"], copy.deepcopy(record)) from exc
            return {"run": copy.deepcopy(record), "replayed": False}

    def rerun_plan(self, source_id):
        from .reruns import fingerprint, AdmissionConflict
        with self.lock:
            source = self.read(source_id)
            if source["status"] not in TERMINAL or source.get("finished_at") is None:
                raise AdmissionConflict("Wait for the original execution to finish before Run again.")
            kind = source.get("kind") or "iperf-run"
            if kind not in {"iperf-run", "protocol-job", "matrix-job", "flent-run", "diagnostic-check"}:
                raise ValueError("This archived execution kind cannot be run again.")
            settings = copy.deepcopy(source["settings"])
            confirmations = []
            if kind == "diagnostic-check":
                from .checks import prepare_check
                check_type, settings = prepare_check(dict(settings, check_type=source["check_type"]))
                config = dict(settings, check_type=check_type)
            elif kind == "matrix-job":
                matrix = source["matrix"]
                config = {"settings": settings, **{key: matrix[key] for key in ("protocols", "modes", "streams")}}
                normalized, _, _ = matrix_plan(config, settings_from)
                config["settings"] = normalized
            elif kind == "flent-run":
                from .flent import settings as flent_settings
                config = {key: settings[key] for key in ("target", "duration", "ip_version", "bind", "label", "notes", "project_id", "tags", "color") if key in settings}
                config.update(workload="rrul", netserver_port=settings["port"])
                flent_settings(dict(config, confirm_netserver=True))  # Validation only; consent is still required at admission.
                confirmations.append("confirm_netserver")
            else:
                config = settings_from(settings, default_mode="bidir")
                if config["role"] == "server":
                    config.update(target="", idle_timeout=min(config["idle_timeout"], 60))
                    confirmations.append("confirm_wait_for_client")
            snapshot = fingerprint({"kind": kind, "config": config, "children": source.get("children", [])})
            return {"schema_version": 1, "source_id": source_id, "kind": kind,
                    "check_type": source.get("check_type"), "config": config, "source_fingerprint": snapshot,
                    "required_confirmations": confirmations,
                    "note": "Creates a new execution using saved configuration notes and metadata; captured results and timeline annotations are not copied. Incoming tests wait once for at most 60 seconds for a client; an accepted test retains the existing total hard deadline of idle_timeout + 3645 seconds."}

    def rerun(self, source_id, data, *, initiator_context=None):
        return self._admit(None, data, source_id=source_id, initiator_context=initiator_context)

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
                               "preflight": child.get("preflight"), "route": child.get("route"),
                               "endpoint_view": endpoint_view(child),
                               "error": child.get("error"), "metrics": measurements(child),
                               "created_at": child["created_at"], "finished_at": child["finished_at"]})
            return result

    job_runs = protocol_runs

    def run_job(self):
        try:
            with self.lock:
                self.job.update(status="running", started_at=now())
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
                    child.update(started_at=now(), status="starting")
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
            self.record["endpoints"] = observe_iperf_start(self.record, data)
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
        if event in ("start", "end", "server_output_json") or "end" in obj:
            self.record["endpoints"] = derive_endpoints(self.record)

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
                        self.record["endpoints"] = derive_endpoints(self.record)
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

    def preflight(self):
        """Checkpoint one independent preflight for this actual outgoing child."""
        from .preflight import PreflightCapture
        settings = self.record["settings"]
        capture = PreflightCapture(settings, lambda: self.cancel_requested or self.shutdown_requested)
        owner_id = self.record["id"]
        with self.lock:
            self.preflight_capture = capture
        def checkpoint(evidence, route):
            with self.lock:
                if self.record["id"] != owner_id:
                    raise RuntimeError("Preflight archive ownership changed.")
                previous = copy.deepcopy(self.record)
                self.record.update(preflight=evidence, route=route)
                try:
                    self.save(self.record)
                except OSError as exc:
                    self.record = previous
                    self.storage_error = str(exc)
                    self.record["error"] = "Preflight archive write failed: " + str(exc)
                    raise
        try:
            capture.capture(checkpoint)
            return not capture.is_cancelled()
        finally:
            capture.cancel()
            with self.lock:
                self.preflight_capture = None

    def run(self, grouped=False):
        readers = []
        diagnostics = None
        try:
            with self.lock:
                self.record["started_at"] = now()
                self.save(self.record)
            settings = self.record["settings"]
            if not self.preflight():
                self.record["status"] = "cancelled"
                return
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
                self.record["status"] = outcome
                self.log("system", f"iPerf3 exited with code {code}; throughput {outcome}.")
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
                self.record["endpoints"] = derive_endpoints(self.record)
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
                    if self.preflight_capture:
                        self.preflight_capture.cancel()
                    if self.diagnostics_capture:
                        self.diagnostics_capture.cancel()
                    if self.process and self.process.poll() is None:
                        self.process.terminate()
                return
            if run_id != self.active:
                self.read(run_id)
                return
            self.cancel_requested = True
            if self.check_capture:
                self.check_capture.cancel()
            try:
                self.log("system", "Stop requested by user.")
            finally:
                if self.preflight_capture:
                    self.preflight_capture.cancel()
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
        if (self.worker and self.worker.is_alive()) or self._check_children_alive() or (self.listener and
                ((self.listener.worker and self.listener.worker.is_alive()) or
                 (self.listener.time_worker and self.listener.time_worker.is_alive()))):
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

    def start_flent(self, data, *, initiator_context=None):
        return self._admit("flent-run", data, initiator_context=initiator_context)["run"]

    def _check_children_alive(self):
        capture = self.check_capture
        if capture is None:
            return False
        for process in list(getattr(capture, "processes", ())):
            try:
                if process.poll() is None:
                    return True
            except OSError:
                return True
        return False

    def run_check(self):
        from .checks import CheckCapture, check_run_status
        capture = result = failure = cleanup_error = None
        try:
            with self.lock:
                self.record["started_at"] = now()
                self.save(self.record)
                owner = self.record["id"]
                capture = CheckCapture(self.record["check_type"], self.record["settings"],
                                       cancelled=lambda: self.cancel_requested or self.shutdown_requested)
                self.check_capture = capture
            def checkpoint(result):
                with self.lock:
                    if self.record["id"] != owner:
                        raise RuntimeError("Check archive ownership changed.")
                    candidate = copy.deepcopy(self.record)
                    snapshot = copy.deepcopy(result)
                    # The adapter can finish measuring before its owned workers
                    # finish cleanup. Persist that evidence as a coherent active
                    # snapshot until the final terminal transaction commits.
                    if check_run_status(snapshot) in TERMINAL:
                        snapshot.update(status="running", finished_at=None)
                    candidate.update(result=snapshot, status=check_run_status(snapshot), finished_at=None,
                                     time_quality=copy.deepcopy(snapshot.get("time_quality")))
                    candidate["endpoints"] = derive_endpoints(candidate)
                    try:
                        self.save(candidate)
                    except OSError as exc:
                        self.storage_error = str(exc)
                        raise
                    self.record = candidate
            result = capture.run(checkpoint)
        except Exception as exc:
            failure = str(exc)
        finally:
            if capture:
                try:
                    capture.cancel()
                except Exception as exc:
                    cleanup_error = "Check child cleanup failed: " + str(exc)
            with self.lock:
                candidate = copy.deepcopy(self.record)
                final_result = copy.deepcopy(result if result is not None else candidate["result"])
                stamp = now()
                if failure is not None:
                    final_result.update(status="cancelled" if self.cancel_requested else "error",
                                        error=failure[:2048], finished_at=stamp)
                elif self.cancel_requested:
                    final_result.update(status="cancelled", finished_at=stamp)
                elif check_run_status(final_result) not in TERMINAL:
                    final_result.update(status="error", error="Check capture ended without a terminal result.", finished_at=stamp)
                owned_child = self._check_children_alive()
                if owned_child or cleanup_error:
                    self.storage_error = cleanup_error or "Check child cleanup did not finish; archive writer ownership is retained until the child exits."
                    final_result.update(status="error", error=self.storage_error[:2048], finished_at=stamp)
                if final_result.get("time_quality", {}).get("status") == "pending":
                    from .time_quality import time_quality_result
                    final_result["time_quality"] = time_quality_result("unavailable", note="The check stopped before a clock observation was retained.")
                candidate.update(result=final_result, status=check_run_status(final_result), finished_at=stamp,
                                 error=final_result.get("error"), time_quality=copy.deepcopy(final_result.get("time_quality")))
                candidate["endpoints"] = derive_endpoints(candidate)
                try:
                    self.save(candidate)
                except OSError as exc:
                    self.storage_error = str(exc)
                    # Do not publish an uncommitted terminal result or admit a
                    # new test over the last durable active checkpoint.
                else:
                    self.record = candidate
                    if not owned_child and not cleanup_error:
                        self.check_capture = None
                        self.active = None

    def run_flent(self):
        from .flent import command, read_result, stop_process
        readers = []
        process = None
        try:
            with self.lock:
                self.record["started_at"] = now()
                self.save(self.record)
            if not self.preflight():
                self.record["status"] = "cancelled"
                self.record["flent"]["status"] = "cancelled"
                return
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
                    self.record["endpoints"] = derive_endpoints(self.record)
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
                self.record["endpoints"] = derive_endpoints(self.record)
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
