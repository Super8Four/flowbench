"""Once-per-test route and read-only local clock evidence before outgoing traffic."""

import copy
from datetime import datetime, timezone
import ipaddress
import math
import os
import re
import subprocess
import threading
import time

from .route import capture_route


CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
STATUSES = {"pending", "captured", "partial", "cancelled", "not_applicable"}
PHASES = {"before_traffic", "observed_after_start"}
ROUTE_STATUSES = {"pending", "captured", "incomplete", "permission_blocked", "unavailable", "error", "timeout", "cancelled", "not_applicable", "skipped"}


def now():
    return datetime.now(timezone.utc).isoformat()


def _utc(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise ValueError("Preflight timestamps must be UTC text.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid preflight timestamp.") from None
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Preflight timestamps must specify UTC.")


def _elapsed(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Preflight elapsed time must be finite and nonnegative.")


def validate_preflight(value):
    from .time_quality import validate_time_quality
    fields = {"schema_version", "policy", "status", "phase", "started_at", "finished_at", "elapsed_seconds", "time_quality"}
    if not isinstance(value, dict) or set(value) != fields or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Invalid preflight schema.")
    if value["policy"] != "once_per_test" or not isinstance(value["status"], str) or value["status"] not in STATUSES or not isinstance(value["phase"], str) or value["phase"] not in PHASES:
        raise ValueError("Invalid preflight policy, phase or status.")
    _utc(value["started_at"])
    if value["finished_at"] is not None:
        _utc(value["finished_at"])
    if value["elapsed_seconds"] is not None:
        _elapsed(value["elapsed_seconds"])
    if (value["finished_at"] is None) != (value["elapsed_seconds"] is None):
        raise ValueError("Preflight completion requires both timestamp and elapsed time.")
    validate_time_quality(value["time_quality"])
    return value


def validate_route(value):
    """Accept legacy route evidence and validate optional new ordered-map fields."""
    if not isinstance(value, dict) or not isinstance(value.get("hops"), list):
        raise ValueError("Saved route must contain a hop array.")
    modern = "phase" in value
    if "output_truncated" in value and not isinstance(value["output_truncated"], bool):
        raise ValueError("Route truncation metadata must be Boolean.")
    if len(value["hops"]) > (16 if modern else 256):
        raise ValueError("Too many route hops.")
    for key, limit in (("status", 64), ("note", 4096), ("raw_output", 32768), ("target", 253)):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > limit):
            raise ValueError("Invalid saved route text.")
    if modern:
        if not isinstance(value["phase"], str) or value["phase"] not in PHASES or not isinstance(value.get("status"), str) or value.get("status") not in ROUTE_STATUSES:
            raise ValueError("Invalid route phase or status.")
        for key in ("started_at", "finished_at"):
            if value.get(key) is not None:
                _utc(value[key])
        if value.get("elapsed_seconds") is not None:
            _elapsed(value["elapsed_seconds"])
    command = value.get("command", [])
    if not isinstance(command, list) or len(command) > 10 or any(not isinstance(arg, str) or not arg or len(arg) > 1024 or "\x00" in arg for arg in command):
        raise ValueError("Invalid saved route command.")
    if modern and command:
        name = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        prefix = ["-d", "-h", "16", "-w", "300"] if name in {"tracert", "tracert.exe"} else ["-n", "-m", "16"] if name == "tracepath" else None
        if prefix is None or command[1:-1] not in (prefix, prefix + ["-4"], prefix + ["-6"]) or command[-1] != value.get("target"):
            raise ValueError("Saved route command must match the bounded native adapter and selected target.")
    previous = 0
    def addresses(items):
        if not isinstance(items, list) or len(items) > 16 or any(not isinstance(item, str) or len(item) > 253 for item in items):
            raise ValueError("Invalid route addresses.")
        for item in items:
            try:
                ipaddress.ip_address(item)
            except ValueError:
                raise ValueError("Route responses require numeric IP addresses.") from None
    def rtts(items):
        if not isinstance(items, list) or len(items) > 64:
            raise ValueError("Invalid route RTT array.")
        for item in items:
            _elapsed(item)
    for hop in value["hops"]:
        if not isinstance(hop, dict) or type(hop.get("hop")) is not int or not 1 <= hop["hop"] <= 16:
            raise ValueError("Invalid saved route hop.")
        if modern and hop["hop"] <= previous:
            raise ValueError("New route hops must be in distinct TTL order.")
        previous = hop["hop"]
        if hop.get("address") is not None and (not isinstance(hop["address"], str) or len(hop["address"]) > 253):
            raise ValueError("Invalid route address.")
        if "addresses" in hop:
            addresses(hop["addresses"])
            if hop.get("address") != (hop["addresses"][0] if hop["addresses"] else None):
                raise ValueError("Route address must agree with observed responses.")
        rtts(hop.get("rtt_ms"))
        if "rtt_upper_bound_ms" in hop:
            rtts(hop["rtt_upper_bound_ms"])
        for key in ("responded", "observed"):
            if key in hop and not isinstance(hop[key], bool):
                raise ValueError("Route observation flags must be Boolean.")
        if "detail" in hop and (not isinstance(hop["detail"], str) or len(hop["detail"]) > 4096):
            raise ValueError("Invalid route detail.")
        if "observations" in hop:
            observations = hop["observations"]
            if not isinstance(observations, list) or len(observations) > 16:
                raise ValueError("Too many route observations.")
            for observation in observations:
                if not isinstance(observation, dict) or set(observation) != {"addresses", "rtt_ms", "rtt_upper_bound_ms", "detail"}:
                    raise ValueError("Invalid route observation.")
                addresses(observation["addresses"])
                rtts(observation["rtt_ms"])
                rtts(observation["rtt_upper_bound_ms"])
                if not isinstance(observation["detail"], str) or len(observation["detail"]) > 2048:
                    raise ValueError("Invalid route observation detail.")
    if "geolocation" in value or any("locations" in hop for hop in value["hops"]):
        from .geolocation import validate_route_geolocation
        validate_route_geolocation(value)
    return value


def initial_preflight(phase="before_traffic", status="pending"):
    from .time_quality import time_quality_result
    return {"schema_version": 1, "policy": "once_per_test", "status": status, "phase": phase,
            "started_at": now(), "finished_at": None, "elapsed_seconds": None,
            "time_quality": time_quality_result(status="pending", note="Local clock evidence has not yet been queried.")}


def route_placeholder(settings, status="pending", phase="before_traffic"):
    return {"status": status, "target": settings.get("target", ""), "hops": [], "raw_output": "", "command": [],
            "phase": phase, "started_at": None, "finished_at": None, "elapsed_seconds": None,
            "note": "One route capture is scheduled before this outgoing test." if status == "pending" else
                    "No route is probed for an externally started incoming test; the peer was unknown before its traffic began."}


class PreflightCapture:
    """Own only preflight children; never assign them to a traffic process group."""
    def __init__(self, settings, cancelled=lambda: False):
        self.settings = dict(settings)
        self.cancelled = cancelled
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.process = None
        self.result = initial_preflight()
        self.route = route_placeholder(settings, "pending" if settings.get("role", "client") == "client" else "not_applicable")

    def is_cancelled(self):
        return self.stop.is_set() or bool(self.cancelled())

    def launch(self, command):
        with self.lock:
            if self.is_cancelled():
                return None
            self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                            text=True, encoding="utf-8", errors="replace", creationflags=CREATE_FLAGS)
            return self.process

    def cancel(self):
        self.stop.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass

    def capture(self, checkpoint):
        from .time_quality import capture_time_quality, time_quality_result
        began = time.monotonic()
        checkpoint(copy.deepcopy(self.result), copy.deepcopy(self.route))
        try:
            if self.settings.get("role", "client") == "client":
                self.route = capture_route(self.settings, self.launch, self.is_cancelled)
        except Exception as exc:
            self.route = route_placeholder(self.settings, "error")
            self.route["note"] = ("Route adapter failed: " + str(exc))[:4096]
        if self.settings.get("role", "client") == "client":
            from .geolocation import enrich_route, geolocation_metadata, empty_location, hop_addresses
            try:
                self.route = enrich_route(self.route)
            except Exception as exc:
                self.route["geolocation"] = geolocation_metadata("error", ("Offline location enrichment failed: " + str(exc))[:1024])
                for hop in self.route.get("hops", []):
                    hop["locations"] = [empty_location(address) for address in hop_addresses(hop)] or [empty_location(None)]
        checkpoint(copy.deepcopy(self.result), copy.deepcopy(self.route))
        try:
            self.result["time_quality"] = (time_quality_result(status="cancelled", note="Preflight was cancelled before the local time query.")
                                             if self.is_cancelled() else capture_time_quality(cancelled=self.is_cancelled, timeout=3))
        except Exception as exc:
            self.result["time_quality"] = time_quality_result(status="error", note=("Local time adapter failed: " + str(exc))[:1024])
        if self.settings.get("role", "client") != "client":
            self.result["time_quality"]["note"] = (self.result["time_quality"].get("note", "") +
                " Sampled before starting a one-test listening server; the external client can connect later.")[:2048]
        self.result.update(finished_at=now(), elapsed_seconds=round(max(0, time.monotonic() - began), 6),
                           status="cancelled" if self.is_cancelled() else "captured" if self.route["status"] in {"captured", "not_applicable"} and
                           self.result["time_quality"]["status"] in {"captured", "status_only", "unsynchronized"} else "partial")
        checkpoint(copy.deepcopy(self.result), copy.deepcopy(self.route))
        return self.result
