"""Run iPerf3 without a shell and retain a replayable archive on disk."""

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
from datetime import datetime, timezone
from uuid import uuid4

from .metrics import effective_settings, measurements, series


TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
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


def settings_from(data):
    if not isinstance(data, dict):
        raise ValueError("Send a JSON object with test settings.")
    result = {}
    for key, default, choices in (
        ("role", "client", {"client", "server"}),
        ("protocol", "tcp", {"tcp", "udp"}),
        ("mode", "upload", {"upload", "download", "bidir"}),
    ):
        value = data.get(key, default)
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"Invalid {key}.")
        result[key] = value
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
        self.events = []
        self.last_checkpoint = 0
        self.capture_errors = []
        self.storage_error = None
        self.executable = executable or self.find_executable()
        self.capabilities = self.probe()
        self.recover()
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
            if self.record and run_id == self.record["id"]:
                return copy.deepcopy(self.record)
            try:
                return json.loads((self.path(run_id) / "run.json").read_text(encoding="utf-8"))
            except FileNotFoundError:
                raise KeyError("Run not found.") from None

    def read_events(self, run_id, after=0):
        with self.lock:
            if run_id == self.active:
                return copy.deepcopy(self.events[after:])
            path = self.path(run_id) / "events.jsonl"
            if not path.exists():
                return []
            events = []
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # A partial last write after abrupt shutdown isn't a valid event.
                    continue
                if event["sequence"] > after:
                    events.append(event)
            return events

    def list_runs(self):
        records = []
        with self.lock:
            for path in self.directory.glob("*/run.json"):
                try:
                    record = self.read(path.parent.name)
                    row = {key: record[key] for key in (
                        "id", "created_at", "finished_at", "status", "settings", "summary", "exit_code"
                    )}
                    row["metrics"] = measurements(record)
                    row["effective_settings"] = effective_settings(record)
                    records.append(row)
                except (KeyError, OSError, json.JSONDecodeError):
                    continue
        return sorted(records, key=lambda row: row["created_at"], reverse=True)

    def save(self, record):
        path = self.path(record["id"])
        path.mkdir(exist_ok=True)
        temp = path / "run.tmp"
        temp.write_text(json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        # OneDrive / antivirus scanners can briefly deny replacement on Windows.
        deadline = time.monotonic() + 5
        while True:
            try:
                temp.replace(path / "run.json")
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)
        self.last_checkpoint = time.monotonic()

    def recover(self):
        for path in self.directory.glob("*/run.json"):
            record = self.read(path.parent.name)
            if record["status"] not in TERMINAL:
                record.update(status="interrupted", finished_at=now(),
                              error="The app stopped before this session finished.")
                self.save(record)

    def command(self, settings):
        cmd = [self.executable]
        if settings["role"] == "server":
            cmd += ["-s", "-1", "--idle-timeout", str(settings["idle_timeout"])]
        else:
            cmd += ["-c", settings["target"], "-t", str(settings["duration"]),
                    "-P", str(settings["streams"]), "--connect-timeout", "5000", "--get-server-output"]
            if settings["protocol"] == "udp":
                if settings["mode"] == "bidir" and not self.capabilities["udp_bidir"]:
                    raise ValueError("Bidirectional UDP is unavailable on this Windows build. Use UDP upload/download or TCP bidirectional.")
                cmd += ["-u", "-b", settings["bandwidth"]]
            if settings["mode"] == "download":
                cmd.append("-R")
            elif settings["mode"] == "bidir":
                if not self.capabilities["bidir"]:
                    raise ValueError("This iPerf3 version does not support bidirectional tests.")
                cmd.append("--bidir")
        cmd += ["-p", str(settings["port"]), "-i", "1", "--forceflush"]
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
        settings = settings_from(data)
        if self.storage_error:
            raise RuntimeError("Archive writes failed. Restore storage access and restart the app: " + self.storage_error)
        if not self.capabilities["available"]:
            raise RuntimeError(self.capabilities["error"])
        command = self.command(settings)
        with self.lock:
            if self.active:
                raise RuntimeError("A session is already running. Stop it before starting another.")
            record = {"schema_version": 1, "id": uuid4().hex, "created_at": now(),
                      "finished_at": None, "status": "starting", "settings": settings,
                      "command": command, "iperf_version": self.capabilities["version"],
                      "output_format": "json-stream" if self.capabilities["json_stream"] else "text",
                      "exit_code": None, "error": None, "summary": {}, "intervals": [],
                      "native_result": None, "test_parameters": None, "event_count": 0}
            self.save(record)
            self.active, self.record = record["id"], record
            self.events, self.cancel_requested = [], False
            self.capture_errors = []
            self.worker = threading.Thread(target=self.run, name=f"iperf-{self.active}", daemon=True)
            self.worker.start()
            return copy.deepcopy(record)

    def log(self, source, message):
        with self.lock:
            event = {"sequence": len(self.events) + 1, "timestamp": now(),
                     "source": source, "message": message}
            # Persist every output line before publishing it to the browser.
            with (self.path(self.active) / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.events.append(event)
            self.record["event_count"] = len(self.events)
            if source == "stdout":
                self.parse(message)
            # Every log entry is durable immediately; avoid rewriting a synced
            # metadata file hundreds of times during the final pretty JSON dump.
            if time.monotonic() - self.last_checkpoint >= .25:
                self.save(self.record)

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

    def run(self):
        readers = []
        try:
            self.log("system", "Starting: " + subprocess.list2cmdline(self.record["command"]))
            with self.lock:
                if not self.cancel_requested:
                    self.process = subprocess.Popen(self.record["command"], stdout=subprocess.PIPE,
                                                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                                    errors="replace", bufsize=1, creationflags=CREATE_FLAGS)
                    self.record["status"] = "running"
                    self.save(self.record)
                else:
                    self.record["status"] = "cancelled"
                    return
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
            with self.lock:
                self.record["exit_code"] = code
                if self.capture_errors:
                    self.record["error"] = "Output capture failed: " + "; ".join(self.capture_errors)
                if self.cancel_requested:
                    self.record["status"] = "cancelled"
                else:
                    self.record["status"] = "completed" if code == 0 and not self.record["error"] else "failed"
                self.log("system", f"iPerf3 exited with code {code}; session {self.record['status']}.")
        except Exception as exc:
            with self.lock:
                self.record.update(status="failed", error=str(exc))
                try:
                    self.log("system", "Session failed: " + str(exc))
                except OSError as log_error:
                    self.storage_error = str(log_error)
        finally:
            with self.lock:
                self.record["finished_at"] = now()
                try:
                    self.save(self.record)
                except OSError as save_error:
                    self.storage_error = str(save_error)
                    self.record.update(status="failed", error="Archive write failed: " + str(save_error))
                finally:
                    self.process = None
                    self.active = None

    def stop(self, run_id):
        with self.lock:
            if run_id != self.active:
                self.read(run_id)
                return
            self.cancel_requested = True
            self.log("system", "Stop requested by user.")
            if self.process and self.process.poll() is None:
                self.process.terminate()

    def shutdown(self):
        if self.active:
            self.stop(self.active)
            if self.worker and self.worker is not threading.current_thread():
                self.worker.join(timeout=5)
        if not self.storage_lock.closed:
            self.storage_lock.close()
        atexit.unregister(self.shutdown)

    def annotate(self, run_id, data):
        if not isinstance(data, dict):
            raise ValueError("Send label and notes as JSON.")
        with self.lock:
            record = self.read(run_id)
            for key, limit in (("label", 200), ("notes", 4000)):
                if key in data:
                    if not isinstance(data[key], str) or len(data[key]) > limit:
                        raise ValueError(f"{key} must be text up to {limit} characters.")
                    record["settings"][key] = data[key].strip()
            if self.record and run_id == self.record["id"]:
                self.record = record
            self.save(record)
            return record

    def export(self, run_id):
        with self.lock:
            record = self.read(run_id)
            record["metrics"] = measurements(record)
            record["effective_settings"] = effective_settings(record)
            record["series"] = series(record)
            events = self.read_events(run_id)
            record["events"] = events
            record["raw_stdout"] = "\n".join(row["message"] for row in events if row["source"] == "stdout")
            record["raw_stderr"] = "\n".join(row["message"] for row in events if row["source"] == "stderr")
            return record
