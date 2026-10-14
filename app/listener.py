"""Independent managed iPerf listener; archive each accepted session once."""

import copy
from datetime import datetime, timezone
import ipaddress
import json
import math
import subprocess
import threading
import time
from uuid import uuid4

from .runner import CREATE_FLAGS, RunManager, now, settings_from


class ListenerConflict(RuntimeError):
    """An existing listener/server owns the requested execution slot or port."""


def listener_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Send listener settings as a JSON object.")
    bind = data.get("bind") or "127.0.0.1"
    if not isinstance(bind, str):
        raise ValueError("Listener bind must be a numeric IP address.")
    try:
        ipaddress.ip_address(bind.split("%")[0])
    except ValueError:
        raise ValueError("Listener bind must be a numeric IP address, such as 127.0.0.1 or ::1.") from None
    settings = settings_from({"role": "server", "bind": bind, "port": data.get("port", 5201),
                              "duration": data.get("max_duration", 3600), "label": data.get("label", "Incoming test"),
                              "notes": data.get("notes", "")})
    return {"bind": settings["bind"], "port": settings["port"], "max_duration": settings["duration"],
            "label": settings["label"], "notes": settings["notes"]}


class IncomingListener:
    parse = RunManager.parse
    parse_text = RunManager.parse_text

    def __init__(self, manager):
        self.manager = manager
        self.lock = manager.lock
        self.config = manager.storage.listener_config() or {**listener_settings({}), "enabled": False}
        self.process = None
        self.worker = None
        self.state = "stopped"
        self.error = None
        self.record = None
        self.events = []
        self.full_lines = []
        self.end_seen = False
        self.last_cookie = None
        self.stopping = False
        self.generation = None
        self.diagnostics = []
        self.time_worker = None
        self.time_cancel = None

    def status(self):
        with self.lock:
            return {"state": self.state, "error": self.error, "settings": copy.deepcopy(self.config),
                    "active_run": self.record["id"] if self.record else None,
                    "diagnostics": list(self.diagnostics), "max_incoming_sessions": 1,
                    "note": "One incoming test at a time. Outbound tests may overlap and interfere with measurements."}

    @property
    def running(self):
        return bool(self.process and self.process.poll() is None)

    def start(self, data=None, persist=True):
        settings = listener_settings(self.config if data is None else data)
        caps = self.manager.capabilities
        if not (caps["available"] and caps["json_stream"] and caps["full_json"] and caps.get("server_max_duration")):
            raise ValueError("Managed capture needs iPerf3 with streaming/full JSON and server duration limits (the bundled 3.22 engine).")
        with self.lock:
            if self.manager.shutdown_requested or self.manager.storage_lock.closed or self.manager.storage_error:
                raise RuntimeError("Restore archive access and restart before enabling the listener.")
            if self.running or (self.worker and self.worker.is_alive()):
                raise ListenerConflict("The managed listener is already running. Stop it before changing settings.")
            if self.manager.active and self.manager.record["settings"]["role"] == "server" and self.manager.record["settings"]["port"] == settings["port"]:
                raise ListenerConflict("A one-test server already uses this port.")
            self.command = [self.manager.executable, "-s", "-B", settings["bind"], "-p", str(settings["port"]),
                            "-i", "1", "--forceflush", "--json-stream", "--json-stream-full-output",
                            "--server-max-duration", str(settings["max_duration"]), "--idle-timeout", "30", "--rcv-timeout", "15000"]
            process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                       bufsize=1, creationflags=CREATE_FLAGS)
            try:
                if persist:
                    self.manager.storage.save_listener_config({**settings, "enabled": True})
            except BaseException:
                process.kill()
                process.wait(timeout=5)
                process.stdout.close()
                raise
            self.config = {**settings, "enabled": True}
            self.process = process
            self.record = None
            self.events, self.full_lines, self.diagnostics = [], [], []
            self.end_seen, self.stopping, self.error = False, False, None
            self.last_cookie = None
            self.generation = uuid4().hex
            self.state = "listening"
            self.worker = threading.Thread(target=self.monitor, name="iperf-incoming", daemon=True)
            self.worker.start()
            return self.status()

    def diagnostic(self, message):
        self.diagnostics.append(message)
        self.diagnostics = self.diagnostics[-100:]

    def begin(self, start):
        if self.record:
            self.finish("interrupted", "A new start arrived before the previous full report.")
        params = start.get("test_start") or {}
        connection = start.get("accepted_connection") or {}
        streams = start.get("connected") or []
        remote = connection.get("host") or (streams[0].get("remote_host") if streams else None)
        settings = settings_from({"role": "server", "bind": self.config["bind"], "port": self.config["port"],
                                  "target": remote or "", "label": self.config["label"], "notes": self.config["notes"]})
        if params:
            settings.update(protocol=str(params.get("protocol", "tcp")).lower(),
                            mode="bidir" if params.get("bidir") else "download" if params.get("reverse") else "upload",
                            # Keep rerun settings within UI limits; exact accepted parameters remain evidence below.
                            duration=max(1, min(3600, int(params.get("duration") or settings["duration"]))),
                            streams=max(1, min(32, int(params.get("num_streams") or settings["streams"]))),
                            bandwidth=str(params.get("target_bitrate", settings["bandwidth"])))
        self.error = None
        observed = now()
        stamp = (start.get("timestamp") or {}).get("timemillisecs")
        seconds = stamp / 1000 if isinstance(stamp, (int, float)) else (start.get("timestamp") or {}).get("timesecs")
        created = observed
        if isinstance(seconds, (int, float)) and math.isfinite(seconds):
            try:
                created = datetime.fromtimestamp(seconds, timezone.utc).isoformat()
            except (ValueError, OSError, OverflowError):
                pass
        self.record = {"schema_version": 1, "id": uuid4().hex, "created_at": created, "finished_at": None,
                       "status": "running", "settings": settings, "command": list(self.command),
                       "iperf_version": start.get("version", self.manager.capabilities["version"]),
                       "output_format": "json-stream", "exit_code": None, "error": None, "summary": {},
                       "intervals": [], "native_result": None, "test_parameters": params or None, "event_count": 0,
                       "incoming": {"listener_id": self.generation, "remote_host": remote,
                                    "remote_port": connection.get("port"), "observed_started_at": observed,
                                    "cookie": start.get("cookie"), "start": copy.deepcopy(start),
                                    "log_stream": "combined stdout/stderr", "directions": "client perspective"}}
        from .preflight import initial_preflight, route_placeholder
        self.record["route"] = route_placeholder(settings, "not_applicable", "observed_after_start")
        self.record["preflight"] = initial_preflight("observed_after_start", "not_applicable")
        self.events, self.full_lines, self.end_seen = [], [], False
        self.manager.save(self.record)
        self.start_time_quality(self.record["id"])
        if self.manager.active and self.manager.record["settings"]["role"] == "client" and self.manager.process and self.manager.process.poll() is None:
            self.mark_overlap(self.manager.record)

    def start_time_quality(self, run_id):
        """Read local clock state after acceptance, outside the measurement lock.

        At most one clock query runs for this listener. A rapid following test
        records unavailable evidence if the preceding bounded query is still
        finishing, rather than spawning an unbounded background queue.
        """
        from .time_quality import time_quality_result
        if self.stopping or (self.time_worker and self.time_worker.is_alive()):
            if self.time_cancel:
                self.time_cancel.set()
            updated = copy.deepcopy(self.record)
            evidence = updated["preflight"]
            evidence.update(finished_at=now(), elapsed_seconds=0.0,
                            time_quality=time_quality_result(status="cancelled" if self.stopping else "unavailable",
                                note="The listener is stopping; no new local time query was launched." if self.stopping else
                                "The previous incoming session's bounded local time query is still finishing. No additional query was queued."))
            self.manager.save(updated)
            self.record = updated
            return
        cancellation = threading.Event()
        self.time_cancel = cancellation
        began = time.monotonic()
        worker = threading.Thread(target=self.capture_time_quality, args=(run_id, cancellation, began),
                                  name="iperf-incoming-clock", daemon=True)
        self.time_worker = worker
        worker.start()

    def capture_time_quality(self, run_id, cancellation, began):
        from .time_quality import capture_time_quality, time_quality_result
        try:
            try:
                quality = capture_time_quality(cancelled=cancellation.is_set, timeout=3)
            except Exception as exc:
                quality = time_quality_result(status="error", note=("Local time adapter failed: " + str(exc))[:1024])
            with self.lock:
                # The test can finish while the read-only query runs. Update its
                # current archived document, never an old pre-summary snapshot.
                active = self.record is not None and self.record["id"] == run_id
                current = self.record if active else self.manager.storage.read(run_id)
                updated = copy.deepcopy(current)
                evidence = updated["preflight"]
                evidence.update(finished_at=now(), elapsed_seconds=round(max(0, time.monotonic() - began), 6), time_quality=quality)
                quality["note"] = (quality.get("note", "") + " Observed after the external client started; this is not a before-traffic snapshot.")[:2048]
                self.manager.save(updated)
                if active:
                    self.record = updated
        except OSError as exc:
            with self.lock:
                self.manager.storage_error = str(exc)
                self.error = "Incoming clock evidence archive write failed: " + str(exc)
                self.state, self.stopping = "failed", True
                if self.process and self.process.poll() is None:
                    self.process.kill()

    def mark_overlap(self, outbound):
        if not self.record:
            return
        incoming = copy.deepcopy(self.record)
        outgoing = copy.deepcopy(outbound)
        for record, other in [(incoming, outgoing), (outgoing, incoming)]:
            overlap = record.setdefault("concurrency", {"overlap_run_ids": [], "note": "Incoming/outbound traffic overlapped; shared resources may affect measurements."})
            if other["id"] not in overlap["overlap_run_ids"]:
                overlap["overlap_run_ids"].append(other["id"])
        with self.manager.storage.transaction() as connection:
            self.manager.storage.save(incoming, connection)
            self.manager.storage.save(outgoing, connection)
        self.record = incoming
        outbound.update(outgoing)

    def log(self, line, source="stdout"):
        previous = copy.deepcopy(self.record)
        event = {"sequence": len(self.events) + 1, "timestamp": now(), "source": source, "message": line}
        try:
            self.record["event_count"] = event["sequence"]
            self.manager.storage.append(self.record, event)
        except OSError:
            self.record = previous
            raise
        self.events.append(event)

    def finish(self, status=None, error=None):
        if not self.record:
            return
        if error:
            self.record["error"] = error
        self.record.update(status=status or ("cancelled" if self.stopping else "failed" if self.record["error"] else "completed"),
                           finished_at=now())
        self.manager.save(self.record)
        self.last_cookie = self.record["incoming"].get("cookie")
        self.record = None
        self.events, self.full_lines, self.end_seen = [], [], False

    def consume_line(self, line):
        with self.lock:
            if self.full_lines:
                if self.record:
                    self.log(line)
                self.full_lines.append(line)
                if line == "}":
                    try:
                        full = json.loads("\n".join(self.full_lines), parse_constant=lambda _: None)
                    except json.JSONDecodeError:
                        return
                    self.full_report(full)
                return
            try:
                item = json.loads(line, parse_constant=lambda _: None)
            except json.JSONDecodeError:
                if line == "{":
                    if self.record:
                        self.full_lines = [line]
                        self.log(line)
                    else:
                        # Rejected accepted connections may have a full error report without a start event.
                        self.full_lines = [line]
                    return
                if self.record:
                    self.log(line, "system")
                else:
                    self.diagnostic(line)
                return
            if not isinstance(item, dict):
                self.diagnostic(line)
                return
            if item.get("event") == "start" and isinstance(item.get("data"), dict):
                self.begin(item["data"])
            if self.record:
                before = copy.deepcopy(self.record)
                try:
                    self.parse(line)
                    if item.get("event") == "end":
                        self.end_seen = True
                    self.log(line)
                except OSError:
                    self.record = before
                    raise
            elif item.get("event"):
                if item.get("event") == "error" and not self.stopping:
                    self.error = self.error or str(item.get("data"))
                self.diagnostic(line)
            if "start" in item and "end" in item:
                self.full_report(item)

    def full_report(self, full):
        start = full.get("start") or {}
        cookie = start.get("cookie")
        if not self.record:
            lines = list(self.full_lines)
            self.full_lines = []
            if cookie and cookie == self.last_cookie:
                return
            if not (start.get("accepted_connection") or start.get("connected")):
                if not self.stopping:
                    self.error = self.error or full.get("error")
                self.diagnostic(json.dumps(full, ensure_ascii=False))
                return
            self.begin(start)
            for line in lines or [json.dumps(full, ensure_ascii=False)]:
                self.log(line)
        self.record.update(native_result=full, summary=full.get("end") or {},
                           intervals=full.get("intervals") or self.record["intervals"],
                           test_parameters=start.get("test_start") or self.record["test_parameters"],
                           error=full.get("error") or self.record["error"])
        self.finish(error=None if self.record["test_parameters"] and self.record["summary"] else
                    self.record["error"] or "Incoming session ended without a complete measurement report.")

    def monitor(self):
        process = self.process
        try:
            for line in iter(process.stdout.readline, ""):
                self.consume_line(line.rstrip("\r\n"))
            code = process.wait(timeout=5)
            with self.lock:
                if self.record:
                    self.record["exit_code"] = code
                    self.finish("cancelled" if self.stopping else "interrupted", "Listener stopped before a complete report.")
                if not self.stopping:
                    self.error = self.error or f"Listener exited with code {code}; restart it explicitly."
                    self.state = "failed"
                else:
                    self.state = "stopped"
        except Exception as exc:
            with self.lock:
                self.error, self.state = str(exc), "failed"
                if isinstance(exc, OSError):
                    self.manager.storage_error = str(exc)
                try:
                    self.finish("failed", "Incoming capture failed: " + str(exc))
                except OSError:
                    pass
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
        finally:
            process.stdout.close()

    def stop(self, disable=True):
        with self.lock:
            storage_failure = None
            try:
                if disable:
                    self.manager.storage.save_listener_config({**self.config, "enabled": False})
                    self.config["enabled"] = False
            except OSError as exc:
                storage_failure = exc
                self.error = "Listener stopped, but disabled setting could not be saved: " + str(exc)
            finally:
                self.stopping = True
                if self.time_cancel:
                    self.time_cancel.set()
                if self.running:
                    self.process.terminate()
            worker = self.worker
        if worker and worker is not threading.current_thread():
            worker.join(5)
            if worker.is_alive():
                if self.running:
                    self.process.kill()
                worker.join(5)
        time_worker = self.time_worker
        if time_worker and time_worker is not threading.current_thread():
            time_worker.join(3.5)
            if time_worker.is_alive():
                self.manager.storage_error = "Incoming time query did not finish; archive writer ownership must be retained."
        if storage_failure:
            raise storage_failure
        return self.status()
