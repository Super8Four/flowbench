"""Typed standalone DNS and ICMP observations; imported evidence is never executed.

Network work happens only in CheckCapture.run, outside admission/archive locks.
DNS and hostname resolution run in owned, cancellable children. Each native ping
is separately owned by the capture; no shared process group or grandchildren.
"""

import copy
from datetime import datetime, timezone
import importlib.metadata
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time

from .annotations import settings_metadata
from .diagnostics import parse_ping_rtt
from .time_quality import capture_time_quality, time_quality_result, validate_time_quality, _iso_utc

CHECK_TYPES = {"dns", "loss"}
RECORD_TYPES = {"A", "AAAA", "CNAME", "MX", "NS", "TXT", "PTR", "SOA", "SRV", "CAA"}
STATUSES = {"pending", "running", "captured", "partial", "cancelled", "interrupted", "unavailable", "timeout", "error"}
DNS_STATUSES = {"answer", "nodata", "nxdomain", "dns_error", "truncated", "timeout", "cancelled", "unavailable", "permission_blocked", "malformed", "error"}
LOSS_STATUSES = {"reply", "no_reply", "unreachable", "timeout", "permission_blocked", "unavailable", "malformed", "cancelled", "error"}
RESPONSES = {"answer", "nodata", "nxdomain", "dns_error", "truncated"}
META = {"label", "notes", "project_id", "tags", "color"}
DNS_FIELDS = {"target", "record_type", "resolver_mode", "resolver_address", "resolver_port", "transport", "count", "timeout_ms"}
LOSS_FIELDS = {"target", "address_family", "count"}
RESULT_FIELDS = {"schema_version", "check_type", "status", "started_at", "finished_at", "elapsed_seconds", "time_quality", "adapter", "resolver", "resolution", "attempts", "summary", "error", "note"}
ATTEMPT_TIMING = {"sequence", "started_at", "finished_at", "duration_ms"}
DNS_ATTEMPT_FIELDS = ATTEMPT_TIMING | {"query_index", "transport", "resolver_address", "resolver_port", "resolver_family", "status", "rcode", "flags", "question", "answers", "authority", "additional", "records_truncated", "raw_output", "output_truncated", "error"}
LOSS_ATTEMPT_FIELDS = ATTEMPT_TIMING | {"address", "address_family", "status", "rtt_ms", "rtt_upper_bound_ms", "transmitted", "echo_replies", "exit_code", "raw_output", "output_truncated", "error"}
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
NOTE = "System UTC is unverified; elapsed durations use a monotonic clock. ICMP echo nonresponse is not application or UDP packet loss."


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _enum(value, allowed, label):
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"Invalid {label}.")
    return value


def _number(value, maximum, label):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"Invalid {label}.")
    return value


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {label}.")
    return value


def _text(value, limit, label, nullable=False):
    if nullable and value is None:
        return value
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
        raise ValueError(f"Invalid or oversized {label}.")
    return value


def _address(value):
    if not isinstance(value, str) or "%" in value or len(value) > 45:
        raise ValueError("Use a plain numeric IPv4 or IPv6 address without an interface scope.")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ValueError("Use a numeric IPv4 or IPv6 address.") from None


def _name(value, absolute=True):
    if not isinstance(value, str) or not value or len(value) > 253 or any(c.isspace() for c in value):
        raise ValueError("Enter a bounded DNS name without spaces or a URL.")
    try:
        result = value.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Invalid DNS name.") from None
    if not all(re.fullmatch(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?", label) for label in result.split(".")) or len(result) > 253:
        raise ValueError("Invalid DNS labels.")
    return result + "." if absolute else result


def prepare_check(data):
    """Normalize input without DNS, subprocesses, or tool installation."""
    if not isinstance(data, dict):
        raise ValueError("Check input must be an object.")
    kind = _enum(data.get("check_type"), CHECK_TYPES, "check type")
    source = data.get("settings", {key: value for key, value in data.items() if key != "check_type"})
    if not isinstance(source, dict) or set(source) - (DNS_FIELDS if kind == "dns" else LOSS_FIELDS) - META:
        raise ValueError("Unsupported check settings.")
    settings = {}
    for key, limit in (("label", 200), ("notes", 4000)):
        value = source.get(key, "")
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f"{key} must be text up to {limit} characters.")
        value.encode("utf-8")
        settings[key] = value
    settings.update(settings_metadata(source))
    target = source.get("target")
    if kind == "dns":
        record_type = _enum(source.get("record_type", "A"), RECORD_TYPES, "DNS record type")
        target = _address(target) if record_type == "PTR" else _name(target)
        mode = _enum(source.get("resolver_mode", "system"), {"system", "custom"}, "resolver mode")
        resolver = source.get("resolver_address")
        if mode == "custom":
            resolver = _address(resolver)
        elif resolver not in (None, ""):
            raise ValueError("A system resolver check cannot specify a custom resolver address.")
        else:
            resolver = None
        settings.update(target=target, record_type=record_type, resolver_mode=mode, resolver_address=resolver,
                        resolver_port=_integer(source.get("resolver_port", 53), 1, 65535, "resolver port"),
                        transport=_enum(source.get("transport", "udp"), {"udp", "tcp"}, "DNS transport"),
                        count=_integer(source.get("count", 1), 1, 5, "DNS query count"),
                        timeout_ms=_integer(source.get("timeout_ms", 2000), 250, 3000, "DNS timeout"))
    else:
        try:
            target = _address(target)
        except ValueError:
            target = _name(target, absolute=isinstance(target, str) and target.endswith("."))
        settings.update(target=target, address_family=_enum(source.get("address_family", "auto"), {"auto", "4", "6"}, "address family"),
                        count=_integer(source.get("count", 10), 1, 60, "ICMP probe count"))
    return kind, settings


def validate_check_settings(check_type, settings):
    kind, normalized = prepare_check({"check_type": check_type, "settings": settings})
    if normalized != settings:
        raise ValueError("Saved check settings must be complete and canonical.")
    return settings


def _version():
    try:
        return importlib.metadata.version("dnspython")
    except importlib.metadata.PackageNotFoundError:
        return None


def summarize_check(check_type, result, settings):
    attempts = result["attempts"]
    if check_type == "dns":
        final = {row["query_index"]: row for row in attempts}
        outcomes = {status: 0 for status in sorted(DNS_STATUSES)}
        for row in final.values():
            outcomes[row["status"]] += 1
        return {"requested_queries": settings["count"], "attempted_queries": len(final), "transport_attempts": len(attempts),
                "responses": sum(row["rcode"] is not None for row in attempts), "outcomes": outcomes}
    sent = sum(row["transmitted"] or 0 for row in attempts)
    replies = sum(row["echo_replies"] or 0 for row in attempts)
    unknown = sum(row["transmitted"] is None or row["echo_replies"] is None for row in attempts)
    values = [row["rtt_ms"] for row in attempts if row["rtt_ms"] is not None]
    return {"requested": settings["count"], "attempted": len(attempts), "unattempted": settings["count"] - len(attempts),
            "transmitted": None if any(row["transmitted"] is None for row in attempts) else sent,
            "confirmed_transmitted": sent, "echo_replies": replies,
            "confirmed_nonresponses": sum(row["transmitted"] == 1 and row["echo_replies"] == 0 for row in attempts),
            "indeterminate_attempts": unknown,
            "nonresponse_percent": 100 * (sent - replies) / sent if not unknown and sent else None,
            "rtt": {"count": len(values), "min_ms": min(values) if values else None, "mean_ms": statistics.mean(values) if values else None,
                    "max_ms": max(values) if values else None, "stdev_ms": statistics.pstdev(values) if values else None}}


def initial_result(check_type, settings):
    validate_check_settings(check_type, settings)
    value = {"schema_version": 1, "check_type": check_type, "status": "pending", "started_at": None, "finished_at": None,
             "elapsed_seconds": 0.0, "time_quality": time_quality_result("pending"),
             "adapter": {"name": "dnspython" if check_type == "dns" else "native_ping", "version": _version() if check_type == "dns" else None, "platform": sys.platform},
             "resolver": None, "resolution": None, "attempts": [], "summary": {}, "error": None, "note": NOTE}
    value["summary"] = summarize_check(check_type, value, settings)
    return value


def check_run_status(result):
    status = _enum(result.get("status"), STATUSES, "check status")
    return {"captured": "completed", "unavailable": "failed", "timeout": "failed", "error": "failed"}.get(status, status)


def check_capabilities():
    """Read configuration/tool presence only; permissions remain untested."""
    dns = {"status": "available" if _version() else "missing_dependency", "adapter": "dnspython", "version": _version(), "configured_resolver": False, "note": "No network capability probe; resolver permissions and reachability are untested."}
    if _version():
        try:
            dns["configured_resolver"] = bool(configured_resolvers())
        except Exception:
            pass
    ping = shutil.which("ping.exe" if sys.platform == "win32" else "ping")
    return {"dns": dns, "loss": {"status": "available" if ping else "missing_tool", "adapter": "native_ping", "version": None,
                                  "note": "Tool presence only; ICMP permissions and output parsing remain untested."}}


def configured_resolvers():
    import dns.resolver
    result = []
    # Resolver construction reads local configuration/registry; it performs no query.
    for item in dns.resolver.Resolver(configure=True).nameservers:
        try:
            address = _address(item)
            if address not in result:
                result.append(address)
        except ValueError:
            continue
        if len(result) == 16:
            break
    return result


def _timing(row):
    _integer(row["sequence"], 1, 60, "attempt sequence")
    _iso_utc(row["started_at"])
    _iso_utc(row["finished_at"])
    _number(row["duration_ms"], 120000, "attempt duration")


def _validate_resolver(value, settings):
    fields = {"mode", "configured_addresses", "selected_address", "port", "address_family"}
    if not isinstance(value, dict) or set(value) != fields or value["mode"] != settings["resolver_mode"] or value["port"] != settings["resolver_port"]:
        raise ValueError("Invalid resolver provenance.")
    addresses = value["configured_addresses"]
    _integer(value["port"], 1, 65535, "resolver port")
    _integer(value["address_family"], 4, 6, "resolver address family")
    if not isinstance(addresses, list) or not 1 <= len(addresses) <= 16 or len(set(_address(a) for a in addresses)) != len(addresses):
        raise ValueError("Invalid configured resolver addresses.")
    selected = _address(value["selected_address"])
    if selected not in addresses or value["address_family"] != ipaddress.ip_address(selected).version:
        raise ValueError("Invalid selected resolver.")
    if settings["resolver_mode"] == "custom" and addresses != [settings["resolver_address"]]:
        raise ValueError("Custom resolver provenance differs from settings.")


def _validate_dns(attempts, settings, resolver):
    if len(attempts) > settings["count"] * 2:
        raise ValueError("Too many DNS transport attempts.")
    previous = None
    name = ipaddress.ip_address(settings["target"]).reverse_pointer + "." if settings["record_type"] == "PTR" else settings["target"]
    for index, row in enumerate(attempts, 1):
        if not isinstance(row, dict) or set(row) != DNS_ATTEMPT_FIELDS:
            raise ValueError("Invalid DNS attempt fields.")
        _timing(row)
        _enum(row["status"], DNS_STATUSES, "DNS outcome")
        query = _integer(row["query_index"], 1, settings["count"], "query index")
        if row["sequence"] != index or resolver is None or row["resolver_address"] != resolver["selected_address"] or row["resolver_port"] != resolver["port"] or row["resolver_family"] != resolver["address_family"]:
            raise ValueError("DNS attempt provenance or sequence differs.")
        _integer(row["resolver_port"], 1, 65535, "attempt resolver port")
        _integer(row["resolver_family"], 4, 6, "attempt resolver family")
        if previous and query == previous["query_index"]:
            if previous["transport"] != "udp" or previous["status"] != "truncated" or row["transport"] != "tcp":
                raise ValueError("Only one explicit TCP fallback after UDP truncation is supported.")
        elif query != (previous["query_index"] + 1 if previous else 1) or row["transport"] != settings["transport"]:
            raise ValueError("DNS logical query sequence differs.")
        if row["question"] != {"name": name, "type": settings["record_type"], "class": "IN"}:
            raise ValueError("DNS question differs from requested check.")
        _enum(row["transport"], {"udp", "tcp"}, "DNS transport")
        if type(row["records_truncated"]) is not bool or type(row["output_truncated"]) is not bool:
            raise ValueError("Invalid DNS truncation metadata.")
        if not isinstance(row["flags"], list) or len(row["flags"]) != len(set(_enum(f, {"QR", "AA", "TC", "RD", "RA", "AD", "CD"}, "DNS flag") for f in row["flags"])):
            raise ValueError("Invalid DNS flags.")
        if row["status"] in RESPONSES:
            if not isinstance(row["rcode"], str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,19}", row["rcode"]) or "QR" not in row["flags"]:
                raise ValueError("A DNS response needs its rcode and response flag.")
            if (row["status"] in {"answer", "nodata"} and row["rcode"] != "NOERROR") or (row["status"] == "nxdomain" and row["rcode"] != "NXDOMAIN") or (row["status"] == "truncated" and "TC" not in row["flags"]):
                raise ValueError("DNS outcome contradicts its rcode/flags.")
            if row["status"] == "dns_error" and row["rcode"] in {"NOERROR", "NXDOMAIN"} and not row["records_truncated"]:
                raise ValueError("DNS error contradicts the response code.")
        elif row["rcode"] is not None or row["flags"]:
            raise ValueError("An unobserved DNS reply cannot have response metadata.")
        total, text_bytes = 0, 0
        for key in ("answers", "authority", "additional"):
            if not isinstance(row[key], list):
                raise ValueError("DNS record sections must be arrays.")
            for record in row[key]:
                if not isinstance(record, dict) or set(record) != {"name", "type", "class", "ttl", "rdata"}:
                    raise ValueError("Invalid DNS resource record.")
                for field, limit in (("name", 1024), ("type", 32), ("class", 32), ("rdata", 4096)):
                    text_bytes += len(_text(record[field], limit, "DNS record text").encode("utf-8"))
                _integer(record["ttl"], 0, 2**32 - 1, "DNS TTL")
                total += 1
        if total > 128 or text_bytes > 32768 or (row["status"] not in RESPONSES and total) or (row["status"] == "answer" and not row["answers"]) or (row["status"] == "nodata" and row["answers"]):
            raise ValueError("DNS records exceed bounds or contradict their outcome.")
        _text(row["raw_output"], 32768, "DNS response output")
        _text(row["error"], 2048, "DNS error", True)
        previous = row


def _validate_loss(attempts, settings, resolution):
    if len(attempts) > settings["count"]:
        raise ValueError("Too many ICMP probes.")
    if resolution is not None:
        fields = {"status", "started_at", "finished_at", "duration_ms", "source", "candidates", "selected_address", "address_family", "error"}
        if not isinstance(resolution, dict) or set(resolution) != fields:
            raise ValueError("Invalid hostname-resolution evidence.")
        _enum(resolution["status"], {"captured", "timeout", "cancelled", "unavailable", "error"}, "resolution status")
        _enum(resolution["source"], {"numeric", "system_getaddrinfo"}, "resolution source")
        _iso_utc(resolution["started_at"]); _iso_utc(resolution["finished_at"])
        _number(resolution["duration_ms"], 4000, "resolution duration")
        candidates = resolution["candidates"]
        if not isinstance(candidates, list) or len(candidates) > 16:
            raise ValueError("Too many resolution candidates.")
        for candidate in candidates:
            if not isinstance(candidate, dict) or set(candidate) != {"address", "address_family"} or ipaddress.ip_address(_address(candidate["address"])).version != candidate["address_family"]:
                raise ValueError("Invalid resolution candidate.")
            _integer(candidate["address_family"], 4, 6, "candidate address family")
        if resolution["status"] == "captured":
            _integer(resolution["address_family"], 4, 6, "selected address family")
            chosen = {"address": resolution["selected_address"], "address_family": resolution["address_family"]}
            if chosen not in candidates or (settings["address_family"] != "auto" and str(chosen["address_family"]) != settings["address_family"]):
                raise ValueError("Invalid pinned destination.")
        elif candidates or resolution["selected_address"] is not None or resolution["address_family"] is not None:
            raise ValueError("Unresolved targets cannot claim an address.")
        _text(resolution["error"], 2048, "resolution error", True)
    for index, row in enumerate(attempts, 1):
        if not isinstance(row, dict) or set(row) != LOSS_ATTEMPT_FIELDS:
            raise ValueError("Invalid ICMP attempt fields.")
        _timing(row)
        _enum(row["status"], LOSS_STATUSES, "ICMP outcome")
        if row["sequence"] != index or not resolution or resolution["status"] != "captured" or row["address"] != resolution["selected_address"] or row["address_family"] != resolution["address_family"]:
            raise ValueError("ICMP probes must use the pinned resolution result.")
        _integer(row["address_family"], 4, 6, "probe address family")
        for field in ("transmitted", "echo_replies"):
            if row[field] is not None:
                _integer(row[field], 0, 1, "ICMP count")
        if row["echo_replies"] == 1 and row["transmitted"] != 1:
            raise ValueError("Echo replies require transmitted evidence.")
        if row["transmitted"] == 0 and row["echo_replies"] != 0:
            raise ValueError("Unsent probes cannot have replies.")
        if row["status"] in {"no_reply", "unreachable"} and (row["transmitted"], row["echo_replies"]) != (1, 0):
            raise ValueError("Nonresponse classification requires observed transmission without an Echo reply.")
        if row["status"] in {"cancelled", "timeout", "malformed", "error", "permission_blocked", "unavailable"} and row["echo_replies"] == 1:
            raise ValueError("Unobserved Echo replies cannot be counted.")
        if row["status"] in {"cancelled", "timeout", "malformed", "error", "permission_blocked", "unavailable"} and row["echo_replies"] == 0 and row["transmitted"] != 0:
            raise ValueError("An incomplete native observation cannot invent an Echo nonresponse count.")
        for field in ("rtt_ms", "rtt_upper_bound_ms"):
            if row[field] is not None:
                _number(row[field], 10000, "ICMP RTT")
        measured = row["rtt_ms"] is not None or row["rtt_upper_bound_ms"] is not None
        if row["rtt_upper_bound_ms"] == 0:
            raise ValueError("A strict RTT upper bound must be positive.")
        if (row["rtt_ms"] is not None and row["rtt_upper_bound_ms"] is not None) or (row["status"] == "reply") != measured or (row["echo_replies"] == 1) != measured:
            raise ValueError("Echo reply counts and measured RTT must agree.")
        if row["exit_code"] is not None:
            _integer(row["exit_code"], -2**31, 2**32 - 1, "native exit code")
        if type(row["output_truncated"]) is not bool:
            raise ValueError("Invalid ICMP output truncation flag.")
        _text(row["raw_output"], 8192, "ICMP output")
        _text(row["error"], 2048, "ICMP error", True)


def _same_summary(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(_same_summary(actual[k], v) for k, v in expected.items())
    if type(expected) in (int, float):
        try:
            return type(actual) in (int, float) and math.isfinite(actual) and actual == expected and (type(expected) is not int or type(actual) is int)
        except OverflowError:
            return False
    return type(actual) is type(expected) and actual == expected


def validate_check_result(check_type, result, settings):
    validate_check_settings(check_type, settings)
    if not isinstance(result, dict) or set(result) != RESULT_FIELDS or type(result["schema_version"]) is not int or result["schema_version"] != 1 or result["check_type"] != check_type:
        raise ValueError("Unsupported diagnostic-check result schema.")
    status = _enum(result["status"], STATUSES, "check status")
    for field in ("started_at", "finished_at"):
        if result[field] is not None:
            _iso_utc(result[field])
    _number(result["elapsed_seconds"], 130, "check elapsed duration")
    if status not in {"pending", "running"} and result["finished_at"] is None:
        raise ValueError("Terminal checks need a finish timestamp.")
    if status == "pending" and (result["started_at"] is not None or result["attempts"]):
        raise ValueError("Pending checks cannot contain executed attempts.")
    validate_time_quality(result["time_quality"])
    if status not in {"pending", "running"} and result["time_quality"]["status"] == "pending":
        raise ValueError("A finished check must describe missing clock evidence as unavailable, not pending.")
    if status == "captured" and result["started_at"] is None:
        raise ValueError("Captured checks require an actual start timestamp.")
    adapter = result["adapter"]
    if not isinstance(adapter, dict) or set(adapter) != {"name", "version", "platform"} or adapter["name"] != ("dnspython" if check_type == "dns" else "native_ping"):
        raise ValueError("Invalid check adapter provenance.")
    _text(adapter["version"], 100, "adapter version", True); _text(adapter["platform"], 100, "adapter platform")
    _text(result["error"], 2048, "check error", True); _text(result["note"], 2048, "check note")
    attempts = result["attempts"]
    if not isinstance(attempts, list):
        raise ValueError("Check attempts must be an array.")
    if check_type == "dns":
        if result["resolution"] is not None:
            raise ValueError("DNS checks never resolve or warm their question name.")
        if result["resolver"] is not None:
            _validate_resolver(result["resolver"], settings)
        _validate_dns(attempts, settings, result["resolver"])
    else:
        if result["resolver"] is not None:
            raise ValueError("ICMP checks cannot claim DNS-query resolver provenance.")
        _validate_loss(attempts, settings, result["resolution"])
    if not _same_summary(result["summary"], summarize_check(check_type, result, settings)):
        raise ValueError("Check summary contradicts recorded attempts.")
    if status == "captured":
        if not _complete(check_type, result, settings):
            raise ValueError("Captured check lacks complete measured evidence.")
    if status == "partial" and not attempts:
        raise ValueError("Partial checks must retain an actual attempt.")
    return result


def validate_check_record(record):
    if not isinstance(record, dict) or record.get("kind") != "diagnostic-check":
        raise ValueError("Invalid diagnostic-check record.")
    kind = _enum(record.get("check_type"), CHECK_TYPES, "check type")
    validate_check_result(kind, record.get("result"), record.get("settings"))
    if record.get("status") != check_run_status(record["result"]):
        raise ValueError("Check lifecycle status contradicts its result.")
    return record


def _complete(kind, result, settings):
    attempts = result["attempts"]
    if kind == "dns":
        final = {row["query_index"]: row for row in attempts}
        return len(final) == settings["count"] and all(row["status"] in RESPONSES - {"truncated"} and not row["records_truncated"] for row in final.values())
    return len(attempts) == settings["count"] and all(row["status"] in {"reply", "no_reply", "unreachable"} and row["transmitted"] is not None and row["echo_replies"] is not None for row in attempts)


def _clip(raw, size):
    encoded = raw.encode("utf-8", errors="replace")
    return encoded[:size].decode("utf-8", errors="ignore"), len(encoded) > size


class CheckCapture:
    def __init__(self, check_type, settings, cancelled=lambda: False):
        validate_check_settings(check_type, settings)
        self.check_type, self.settings = check_type, copy.deepcopy(settings)
        self.cancelled = cancelled
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.processes = set()
        self.result = initial_result(check_type, settings)
        self.last_good = copy.deepcopy(self.result)
        self.checkpoint_failed = False

    def _cancelled(self):
        return self.stop.is_set() or bool(self.cancelled())

    def cancel(self):
        self.stop.set()
        with self.lock:
            children = list(self.processes)
        for process in children:
            try:
                process.kill()
            except OSError:
                pass

    def _process(self, command, timeout, payload=None, limit=131072, on_line=None, cwd=None):
        """Drain bounded output concurrently; stop/reap within the supplied budget."""
        if self._cancelled():
            return {"status": "cancelled", "raw": "", "exit_code": None, "truncated": False}
        process = reader = None
        output = bytearray()
        oversized = threading.Event()
        deadline = time.monotonic() + max(.05, timeout - .25)
        status = "ok"
        delivered = 0
        def deliver_lines():
            nonlocal delivered
            if on_line is not None:
                end = output.rfind(b"\n") + 1
                if end > delivered:
                    lines = bytes(output[delivered:end]).splitlines()
                    delivered = end
                    for line in lines:
                        on_line(line.decode("utf-8", errors="replace"))
        try:
            process = subprocess.Popen(command, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
                                       creationflags=CREATE_FLAGS, env={**os.environ, "LC_ALL": "C", "LANG": "C"}, cwd=cwd)
            with self.lock:
                self.processes.add(process)
            def drain():
                try:
                    while True:
                        part = os.read(process.stdout.fileno(), 1024)
                        if not part:
                            break
                        remaining = limit - len(output)
                        output.extend(part[:remaining])
                        if len(part) > remaining:
                            oversized.set()
                except (OSError, ValueError):
                    pass
            reader = threading.Thread(target=drain, daemon=True, name="flowbench-check-output")
            reader.start()
            if payload is not None:
                process.stdin.write(json.dumps(payload, allow_nan=False, ensure_ascii=False).encode("utf-8"))
                process.stdin.close()
            while process.poll() is None:
                deliver_lines()
                if self._cancelled() or time.monotonic() >= deadline or oversized.is_set():
                    status = "cancelled" if self._cancelled() else "malformed" if oversized.is_set() else "timeout"
                    process.kill()
                    break
                self.stop.wait(.02)
            process.wait(timeout=.15)
            reader.join(timeout=.05)
            deliver_lines()
            raw, cut = _clip(bytes(output).decode("utf-8", errors="replace"), limit)
            if self._cancelled():
                status = "cancelled"
            return {"status": status, "raw": raw, "exit_code": process.returncode, "truncated": oversized.is_set() or cut}
        except PermissionError as exc:
            if self.checkpoint_failed:
                raise
            return {"status": "permission_blocked", "raw": str(exc)[:2048], "exit_code": None, "truncated": False}
        except OSError as exc:
            if self.checkpoint_failed:
                raise
            return {"status": "unavailable", "raw": str(exc)[:2048], "exit_code": None, "truncated": False}
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "raw": bytes(output).decode("utf-8", errors="replace"), "exit_code": None, "truncated": oversized.is_set()}
        finally:
            if process:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=.1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                with self.lock:
                    if process.poll() is not None:
                        self.processes.discard(process)
                if reader:
                    reader.join(timeout=.05)
                if process.poll() is None:
                    raise RuntimeError("Owned check process did not stop within its cleanup budget.")
                if process.stdout:
                    process.stdout.close()
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()

    def _worker(self, payload, timeout, on_attempt=None):
        attempts = []
        def receive(line):
            value = json.loads(line)
            if isinstance(value, dict) and "attempt" in value:
                attempts.append(value["attempt"])
                if on_attempt is not None:
                    on_attempt(value["attempt"])
        observed = self._process([sys.executable, "-m", "app.check_worker"], timeout, payload, limit=1048576,
                                 on_line=receive if payload.get("operation") == "dns" else None,
                                 cwd=str(Path(__file__).resolve().parent.parent))
        if observed["status"] != "ok":
            return {"status": observed["status"], "error": observed["raw"][:2048] or "Owned worker did not return a complete observation."}
        try:
            value = json.loads(observed["raw"].splitlines()[-1])
            if not isinstance(value, dict):
                raise ValueError("Worker returned a nonobject.")
            if attempts:
                value["attempts"] = attempts
            return value
        except (ValueError, TypeError):
            return {"status": "malformed", "error": "Owned worker returned unsupported or oversized output."}

    def _checkpoint(self, callback):
        self.result["summary"] = summarize_check(self.check_type, self.result, self.settings)
        validate_check_result(self.check_type, self.result, self.settings)
        try:
            callback(copy.deepcopy(self.result))
        except Exception:
            self.checkpoint_failed = True
            raise
        self.last_good = copy.deepcopy(self.result)

    def _resolve(self, deadline):
        start, stamp = time.monotonic(), utc_now()
        target = self.settings["target"]
        try:
            ip = ipaddress.ip_address(target)
            family = self.settings["address_family"]
            value = {"status": "captured", "candidates": [{"address": str(ip), "address_family": ip.version}]} if family == "auto" or family == str(ip.version) else {"status": "error", "error": "Numeric target conflicts with the selected address family."}
            source = "numeric"
        except ValueError:
            source = "system_getaddrinfo"
            value = self._worker({"operation": "resolve", "target": target, "address_family": self.settings["address_family"]}, min(3, max(.05, deadline - time.monotonic())))
        candidates = value.get("candidates", []) if value.get("status") == "captured" else []
        if not candidates:
            value["status"] = value.get("status", "error") if value.get("status") != "captured" else "error"
        selected = candidates[0] if candidates else {}
        return {"status": value.get("status", "error") if value.get("status") in {"captured", "timeout", "cancelled", "unavailable", "error"} else "error", "started_at": stamp,
                "finished_at": utc_now(), "duration_ms": (time.monotonic() - start) * 1000, "source": source,
                "candidates": candidates, "selected_address": selected.get("address"), "address_family": selected.get("address_family"), "error": value.get("error")}

    def _ping(self, sequence, destination, deadline):
        start, stamp = time.monotonic(), utc_now()
        address, family = destination["selected_address"], destination["address_family"]
        executable = shutil.which("ping.exe" if sys.platform == "win32" else "ping")
        command = [executable, "-n", "1", "-w", "1000", "-4" if family == 4 else "-6", address] if sys.platform == "win32" else [executable, "-n", "-c", "1", "-W", "1", "-4" if family == 4 else "-6", address]
        observed = self._process(command, min(1.5, max(.05, deadline - start)), limit=8192) if executable else {"status": "unavailable", "raw": "Native ping is unavailable.", "exit_code": None, "truncated": False}
        raw, status = observed["raw"], observed["status"]
        rtt = upper = None
        sent = replies = None
        if status in {"unavailable", "permission_blocked"} and observed["exit_code"] is None:
            sent = replies = 0
        if status == "ok":
            rtt, upper = parse_ping_rtt(raw)
            permission = re.search(r"permission denied|operation not permitted|access is denied|requires.*privilege", raw, re.I)
            linux = re.search(r"(?m)^\s*(\d+) packets transmitted,\s*(\d+) (?:packets )?received", raw)
            windows = re.search(r"(?m)^\s*[^\r\n=]*=\s*(\d+)\s*,[^\r\n=]*=\s*(\d+)\s*,[^\r\n=]*=\s*(\d+)\s*\(", raw)
            if permission:
                status = "permission_blocked"
                rtt = upper = None
            elif rtt is not None or upper is not None:
                status, sent, replies = "reply", 1, 1
            else:
                parsed = linux or windows
                if parsed and int(parsed[1]) == 1:
                    sent = 1
                unreachable = re.search(r"unreachable|no route to host|destination host.*not reachable", raw, re.I)
                no_reply = re.search(r"request timed out|100(?:[.,]0+)?% (?:packet )?loss", raw, re.I)
                if sent == 1 and (unreachable or no_reply):
                    status, replies = "unreachable" if unreachable else "no_reply", 0
                else:
                    status = "malformed"
        return {"sequence": sequence, "started_at": stamp, "finished_at": utc_now(), "duration_ms": (time.monotonic() - start) * 1000,
                "address": address, "address_family": family, "status": status, "rtt_ms": rtt, "rtt_upper_bound_ms": upper,
                "transmitted": sent, "echo_replies": replies, "exit_code": observed["exit_code"], "raw_output": raw,
                "output_truncated": observed["truncated"], "error": None if status in {"reply", "no_reply", "unreachable"} else "Echo/transmission evidence unavailable; native exit code alone is insufficient."}

    def run(self, checkpoint=lambda result: None):
        started = time.monotonic()
        deadline = started + (30 if self.check_type == "dns" else 120)
        self.result.update(status="running", started_at=utc_now())
        try:
            self._checkpoint(checkpoint)
            try:
                self.result["time_quality"] = capture_time_quality(self._cancelled, timeout=min(3, deadline - time.monotonic()))
            except Exception as exc:
                self.result["time_quality"] = time_quality_result("error", str(exc)[:1000])
            self._checkpoint(checkpoint)
            if not self._cancelled():
                try:
                    if self.check_type == "dns":
                        self._run_dns(checkpoint, deadline)
                    else:
                        self._run_loss(checkpoint, deadline)
                except Exception as exc:
                    if self.checkpoint_failed:
                        raise
                    self.result = copy.deepcopy(self.last_good)
                    self.result.update(status="error", error=str(exc)[:2048])
            if self._cancelled():
                status = "cancelled"
            elif _complete(self.check_type, self.result, self.settings):
                status = "captured"
            elif self.result["attempts"]:
                statuses = {row["status"] for row in self.result["attempts"]}
                status = "timeout" if statuses == {"timeout"} else "partial" if any(row["status"] in RESPONSES | {"reply", "no_reply", "unreachable"} for row in self.result["attempts"]) else "error"
            else:
                status = self.result["status"] if self.result["status"] in {"unavailable", "timeout", "error"} else "error"
            self.result.update(status=status, finished_at=utc_now(), elapsed_seconds=time.monotonic() - started)
            self._checkpoint(checkpoint)
            validate_check_result(self.check_type, self.result, self.settings)
            return copy.deepcopy(self.result)
        finally:
            self.cancel()

    def _run_dns(self, checkpoint, deadline):
        if not _version():
            self.result.update(status="unavailable", error="dnspython is not installed.")
            return
        try:
            addresses = [self.settings["resolver_address"]] if self.settings["resolver_mode"] == "custom" else configured_resolvers()
        except Exception as exc:
            self.result.update(status="unavailable", error=f"Local resolver configuration unavailable: {exc}"[:2048])
            return
        if not addresses:
            self.result.update(status="unavailable", error="No numeric resolver is locally configured; no public fallback was used.")
            return
        selected = addresses[0]
        self.result["resolver"] = {"mode": self.settings["resolver_mode"], "configured_addresses": addresses, "selected_address": selected, "port": self.settings["resolver_port"], "address_family": ipaddress.ip_address(selected).version}
        self._checkpoint(checkpoint)
        for query in range(1, self.settings["count"] + 1):
            if self._cancelled() or time.monotonic() >= deadline:
                break
            began = time.monotonic()
            began_at = utc_now()
            before = len(self.result["attempts"])
            def completed_attempt(row):
                row["sequence"] = len(self.result["attempts"]) + 1
                candidate = self.result["attempts"] + [row]
                _validate_dns(candidate, self.settings, self.result["resolver"])
                self.result["attempts"].append(row)
                self._checkpoint(checkpoint)
            # Only query configuration enters the child, never private labels,
            # notes, projects or tags that cannot affect a DNS question.
            query_settings = {key: self.settings[key] for key in DNS_FIELDS}
            value = self._worker({"operation": "dns", "settings": query_settings, "resolver": self.result["resolver"], "query_index": query}, min(self.settings["timeout_ms"] / 1000 + 1, deadline - began), on_attempt=completed_attempt)
            if len(self.result["attempts"]) > before:
                rows = []
            else:
                rows = value.get("attempts")
            if len(self.result["attempts"]) == before and (not isinstance(rows, list) or not rows):
                from .check_worker import dns_attempt
                row = dns_attempt(self.settings, self.result["resolver"], query, self.settings["transport"])
                row.update(status=value.get("status") if value.get("status") in DNS_STATUSES else "error", error=str(value.get("error", "DNS worker failed."))[:2048],
                           started_at=began_at, finished_at=utc_now(), duration_ms=(time.monotonic() - began) * 1000)
                rows = [row]
            for row in rows:
                completed_attempt(row)
            delay = max(0, 1 - (time.monotonic() - began))
            if query < self.settings["count"]:
                self.stop.wait(min(delay, max(0, deadline - time.monotonic())))

    def _run_loss(self, checkpoint, deadline):
        self.result["resolution"] = self._resolve(deadline)
        self._checkpoint(checkpoint)
        if self.result["resolution"]["status"] != "captured":
            self.result.update(status=self.result["resolution"]["status"], error=self.result["resolution"]["error"])
            return
        if not shutil.which("ping.exe" if sys.platform == "win32" else "ping"):
            self.result.update(status="unavailable", error="Native ping is unavailable; no packets were counted.")
            return
        for sequence in range(1, self.settings["count"] + 1):
            if self._cancelled() or time.monotonic() >= deadline:
                break
            began = time.monotonic()
            row = self._ping(sequence, self.result["resolution"], deadline)
            self.result["attempts"].append(row)
            self._checkpoint(checkpoint)
            if row["status"] in {"unavailable", "permission_blocked"}:
                break
            if sequence < self.settings["count"]:
                self.stop.wait(min(max(0, 1 - (time.monotonic() - began)), max(0, deadline - time.monotonic())))
