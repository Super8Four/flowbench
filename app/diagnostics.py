"""Opt-in, bounded diagnostics for the local endpoint and the selected peer.

ICMP echo replies describe probe RTT, never application latency or a proven
throughput bottleneck. No diagnostic subprocess shares the iPerf process slot.
"""

from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import threading
import time


CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_LOADED_SAMPLES = 120
PROBE_INTERVAL = 1.0


def diagnostic_capabilities():
    return {"latency": {"available": bool(shutil.which("ping")), "tool": "native ping", "metric": "ICMP echo RTT"},
            "endpoint_health": {"available": bool(shutil.which("powershell")) if os.name == "nt" else Path("/proc/stat").is_file(),
                                "scope": "local only", "tool": "Windows CIM/adapter counters" if os.name == "nt" else "Linux /proc"},
            "mtu": {"available": bool(shutil.which("ping")), "tool": "native ping", "note": "Windows IPv6 fragmentation checks are unavailable."},
            "route": {"available": bool(shutil.which("tracert" if os.name == "nt" else "tracepath")),
                      "tool": "tracert" if os.name == "nt" else "tracepath"}}


def validate_diagnostics(value):
    """Validate portable diagnostic evidence without executing imported commands."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
        raise ValueError("Unsupported diagnostics schema.")
    if set(value) != {"schema_version", "latency", "endpoint_health", "path_context"}:
        raise ValueError("Invalid diagnostics fields.")
    for key in ("latency", "endpoint_health", "path_context"):
        if not isinstance(value[key], dict):
            raise ValueError(f"Invalid diagnostics {key}.")
    nodes = [value]
    count = 0
    while nodes:
        node = nodes.pop()
        count += 1
        if count > 20000:
            raise ValueError("Diagnostic evidence is too large.")
        if isinstance(node, dict):
            if len(node) > 64 or any(not isinstance(key, str) or len(key) > 100 for key in node):
                raise ValueError("Invalid diagnostic object.")
            for key, item in node.items():
                if key in ("rtt_ms", "rtt_upper_bound_ms") and item is not None and (isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item < 0):
                    raise ValueError("Invalid diagnostic RTT.")
                nodes.append(item)
        elif isinstance(node, list):
            if len(node) > 128:
                raise ValueError("Too many diagnostic samples or interfaces.")
            nodes.extend(node)
        elif isinstance(node, str):
            if len(node) > 4096:
                raise ValueError("Diagnostic text is too long.")
        elif isinstance(node, float) and not math.isfinite(node):
            raise ValueError("Diagnostic numbers must be finite.")
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            raise ValueError("Invalid diagnostic value.")
    latency = value["latency"]
    if not isinstance(latency.get("enabled"), bool) or latency.get("metric") != "ICMP echo RTT":
        raise ValueError("Invalid diagnostic latency metric.")
    if not isinstance(value["endpoint_health"].get("enabled"), bool):
        raise ValueError("Invalid diagnostic health flag.")
    for name, limit in (("baseline", 3), ("loaded", MAX_LOADED_SAMPLES)):
        section = latency.get(name)
        if not isinstance(section, dict) or not isinstance(section.get("samples"), list) or len(section["samples"]) > limit:
            raise ValueError("Invalid diagnostic latency samples.")
        if any(not isinstance(sample, dict) or sample.get("status") not in {"reply", "no_reply", "timeout", "cancelled", "unavailable", "permission_blocked", "error"} for sample in section["samples"]):
            raise ValueError("Invalid diagnostic sample status.")
        for sample in section["samples"]:
            if sample.get("rtt_ms") is not None and sample.get("status") != "reply":
                raise ValueError("A missing reply cannot contain measured RTT.")
            elapsed = sample.get("elapsed_seconds")
            if elapsed is not None and (isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0):
                raise ValueError("Invalid diagnostic elapsed time.")
        expected = latency_summary(section["samples"])
        summary = section.get("summary")
        if not isinstance(summary, dict) or set(summary) != set(expected):
            raise ValueError("Invalid diagnostic latency summary.")
        for key, computed in expected.items():
            actual = summary[key]
            if actual is None and computed is None:
                continue
            if isinstance(actual, bool) or not isinstance(actual, (int, float)) or computed is None or not math.isclose(actual, computed, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("Diagnostic summary does not match its measured samples.")
    mtu = value["path_context"].get("mtu")
    if not isinstance(mtu, dict) or not isinstance(mtu.get("tested_packet_sizes"), list) or len(mtu["tested_packet_sizes"]) > 3:
        raise ValueError("Invalid diagnostic MTU evidence.")
    replies = []
    for sample, size in zip(mtu["tested_packet_sizes"], (1500, 1400, 1280)):
        if not isinstance(sample, dict) or sample.get("packet_bytes") != size or sample.get("status") not in {"reply", "no_reply", "timeout", "cancelled", "unavailable", "permission_blocked", "error"}:
            raise ValueError("Invalid diagnostic MTU sample.")
        if sample["status"] == "reply":
            replies.append(size)
    if mtu.get("largest_confirmed_packet_bytes") != (max(replies) if replies else None):
        raise ValueError("Confirmed packet size must match its measured echo reply.")
    return value


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def probe_target(value):
    """Keep arguments a validated single host, including scoped IPv6 addresses."""
    if not isinstance(value, str) or not value or len(value) > 253:
        return None
    try:
        ipaddress.ip_address(value.split("%")[0])
        return value if "%" not in value or re.fullmatch(r"[\w.:-]+%[\w.-]+", value) else None
    except ValueError:
        labels = value.rstrip(".").split(".")
        return value if all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) for part in labels) else None


def parse_ping_rtt(output):
    """Only numeric, measured echo reply times; '<1ms' is a bound, not zero."""
    match = re.search(r"(?:time|temps|zeit)\s*([=<])\s*(\d+(?:[.,]\d+)?)\s*ms", output, re.I)
    if not match:
        return None, None
    value = float(match[2].replace(",", "."))
    if not math.isfinite(value) or value < 0:
        return None, None
    return (None, value) if match[1] == "<" else (value, None)


def latency_summary(samples):
    values = [sample["rtt_ms"] for sample in samples if sample.get("rtt_ms") is not None]
    replies = sum(sample.get("status") == "reply" for sample in samples)
    ordered = sorted(values)
    return {"attempted": len(samples), "replies": replies, "missing": len(samples) - replies,
            "numeric_samples": len(values), "min_ms": min(values) if values else None,
            "max_ms": max(values) if values else None,
            "mean_ms": statistics.mean(values) if values else None,
            "median_ms": statistics.median(values) if values else None,
            "stddev_ms": statistics.pstdev(values) if values else None,
            "p95_ms": ordered[max(0, math.ceil(len(values) * .95) - 1)] if values else None}


def phase_status(samples):
    if not samples:
        return "unavailable"
    statuses = {sample["status"] for sample in samples}
    if statuses == {"reply"}:
        return "captured"
    if "reply" in statuses:
        return "partial"
    return next(iter(statuses)) if len(statuses) == 1 else "partial"


def linux_snapshot(proc_root=Path("/proc")):
    result = {"status": "unavailable", "source": "Linux /proc", "captured_at": timestamp(),
              "cpu": None, "interfaces": [], "note": "Counters belong to the local OS network namespace; no remote counters are inferred."}
    errors = []
    try:
        fields = (proc_root / "stat").read_text(encoding="ascii").splitlines()[0].split()
        ticks = [int(value) for value in fields[1:9]]
        if fields[0] != "cpu" or len(ticks) < 4:
            raise ValueError("CPU counters are incomplete")
        result["cpu"] = {"total_ticks": sum(ticks), "idle_ticks": ticks[3] + (ticks[4] if len(ticks) > 4 else 0),
                         "source": "local aggregate CPU counters"}
    except (OSError, ValueError, IndexError) as exc:
        errors.append(f"CPU unavailable: {exc}")
    try:
        for row in (proc_root / "net" / "dev").read_text(encoding="utf-8", errors="replace").splitlines()[2:]:
            name, counters = row.split(":", 1)
            values = [int(value) for value in counters.split()]
            if len(values) < 16:
                raise ValueError("Interface counters are incomplete")
            result["interfaces"].append({"name": name.strip(), "rx_bytes": values[0], "rx_errors": values[2],
                                         "rx_drops": values[3], "tx_bytes": values[8], "tx_errors": values[10], "tx_drops": values[11]})
            if len(result["interfaces"]) >= 128:
                result["note"] += " Interface capture is capped at 128 adapters."
                break
    except (OSError, ValueError) as exc:
        errors.append(f"Interface counters unavailable: {exc}")
    result["status"] = "captured" if result["cpu"] is not None and not errors else "partial" if result["cpu"] or result["interfaces"] else "unavailable"
    if errors:
        result["errors"] = errors
    return result


def health_delta(before, after):
    result = {"cpu_utilization_percent": None, "interfaces": [],
              "note": "Local counter changes include other traffic and processes; they do not establish an iPerf or path bottleneck."}
    start, end = before.get("cpu"), after.get("cpu")
    if start and end and "total_ticks" in start and "total_ticks" in end:
        total = end["total_ticks"] - start["total_ticks"]
        idle = end["idle_ticks"] - start["idle_ticks"]
        if total > 0 and 0 <= idle <= total:
            result["cpu_utilization_percent"] = round(100 * (total - idle) / total, 2)
    old = {item["name"]: item for item in before.get("interfaces", [])}
    for item in after.get("interfaces", []):
        previous = old.get(item["name"])
        entry = {"name": item["name"], "status": "unavailable", "counters": {}}
        if previous:
            for key in ("rx_bytes", "rx_errors", "rx_drops", "tx_bytes", "tx_errors", "tx_drops"):
                left, right = previous.get(key), item.get(key)
                entry["counters"][key] = right - left if isinstance(left, int) and isinstance(right, int) and right >= left else None
            entry["status"] = "captured" if all(value is not None for value in entry["counters"].values()) else "partial"
        result["interfaces"].append(entry)
    return result


class DiagnosticsCapture:
    """Lifecycle: baseline before traffic, start at traffic, finish after traffic.

    cancel() terminates only owned diagnostic children. finish() is idempotent;
    callers must finish even when traffic fails. cancel is also checked during
    each wait. Raw ping output is bounded and retained when parsing is unavailable.
    """

    def __init__(self, settings, cancelled=lambda: False):
        self.settings = dict(settings)
        self.cancelled = cancelled
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.processes = set()
        self.worker = None
        self.started = None
        self.result = None
        self.cancel_requested = False
        self.baseline_done = False
        self.on_update = None
        self.checkpoint_error = None
        self.before = None
        self.baseline_samples = []
        self.loaded_samples = []
        self.baseline_status = "disabled"
        self.target = probe_target(settings.get("target")) if settings.get("role", "client") == "client" else None

    def _cancelled(self):
        return self.stop.is_set() or self.cancel_requested or bool(self.cancelled())

    def _command(self, command, timeout=1.2):
        if self._cancelled():
            return {"status": "cancelled", "output": "", "exit_code": None}
        process = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", creationflags=CREATE_FLAGS)
            with self.lock:
                self.processes.add(process)
            deadline = time.monotonic() + timeout
            while True:
                if self._cancelled() or time.monotonic() >= deadline:
                    status = "cancelled" if self._cancelled() else "timeout"
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        output, _ = process.communicate(timeout=.3)
                    except subprocess.TimeoutExpired:
                        output = ""
                    return {"status": status, "output": output[-4096:], "exit_code": process.returncode}
                try:
                    output, _ = process.communicate(timeout=min(.1, max(.001, deadline - time.monotonic())))
                    return {"status": "cancelled" if self._cancelled() else "completed", "output": output[-4096:], "exit_code": process.returncode}
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            return {"status": "permission_blocked" if isinstance(exc, PermissionError) else "unavailable" if isinstance(exc, FileNotFoundError) else "error", "output": str(exc), "exit_code": None}
        finally:
            if process:
                with self.lock:
                    self.processes.discard(process)

    def _family(self):
        if self.settings.get("ip_version") in ("4", "6"):
            return self.settings["ip_version"]
        try:
            return str(ipaddress.ip_address(self.target.split("%")[0]).version)
        except ValueError:
            return "4"

    def _ping_command(self, size=None):
        tool = shutil.which("ping")
        if not self.target or not tool:
            return None
        family = self._family()
        if os.name == "nt":
            if size is not None and family == "6":
                return None
            return [tool, "-" + family, "-n", "1", "-w", "900", *(["-f", "-l", str(size - 28)] if size else []), self.target]
        return [tool, "-" + family, "-n", "-c", "1", "-W", "1",
                *(["-M", "do", "-s", str(size - (48 if family == "6" else 28))] if size else []), self.target]

    def _probe(self, phase, size=None):
        sample = {"timestamp": timestamp(), "elapsed_seconds": round(time.monotonic() - self.started, 3) if phase == "loaded" else None,
                  "status": "unavailable", "rtt_ms": None, "rtt_upper_bound_ms": None}
        command = self._ping_command(size)
        if not command:
            sample["note"] = "Ping tool or a supported selected client target is unavailable."
            return sample
        command_result = self._command(command)
        value, bound = parse_ping_rtt(command_result["output"])
        sample.update(status=command_result["status"], exit_code=command_result["exit_code"], raw_output=command_result["output"])
        if command_result["status"] == "completed":
            sample["status"] = "reply" if command_result["exit_code"] == 0 else "permission_blocked" if re.search(r"permission|not permitted|access.*denied|requires.*privilege", command_result["output"], re.I) else "error" if command_result["exit_code"] not in (0, 1) else "no_reply"
            if sample["status"] == "reply":
                sample.update(rtt_ms=value, rtt_upper_bound_ms=bound)
                if value is None and bound is None:
                    sample["note"] = "Echo succeeded but the localized reply time could not be parsed."
        return sample

    def baseline(self):
        first = not self.baseline_done
        self.baseline_done = True
        if self.settings.get("latency") and self.baseline_status == "disabled":
            self.baseline_status = "captured"
            deadline = time.monotonic() + 4
            for _ in range(3):
                if self._cancelled() or time.monotonic() >= deadline:
                    self.baseline_status = "cancelled" if self._cancelled() else "partial"
                    break
                self.baseline_samples.append(self._probe("baseline"))
                if self.baseline_samples[-1]["status"] == "unavailable":
                    self.baseline_status = "unavailable"
                    break
            if self.baseline_status == "captured":
                self.baseline_status = phase_status(self.baseline_samples)
        if first and self.settings.get("endpoint_health") and not self._cancelled():
            self.before = self._health()
        return {"status": self.baseline_status, "samples": self.baseline_samples,
                "summary": latency_summary(self.baseline_samples)}

    def _health(self):
        if os.name != "nt":
            return linux_snapshot()
        tool = shutil.which("powershell")
        if not tool:
            return {"status": "unavailable", "source": "Windows local counters", "captured_at": timestamp(), "cpu": None, "interfaces": []}
        # Fixed read-only cmdlets. No supplied script, interface, credentials or remote session.
        script = "$ErrorActionPreference='Stop'; $c=Get-CimInstance Win32_Processor; $n=Get-NetAdapterStatistics; @{cpu=@($c|ForEach-Object {$_.LoadPercentage}); interfaces=@($n|ForEach-Object {@{name=$_.Name; rx_bytes=$_.ReceivedBytes; rx_errors=$_.ReceivedPacketErrors; rx_drops=$_.ReceivedDiscardedPackets; tx_bytes=$_.SentBytes; tx_errors=$_.OutboundPacketErrors; tx_drops=$_.OutboundDiscardedPackets}})}|ConvertTo-Json -Depth 4 -Compress"
        captured = self._command([tool, "-NoProfile", "-NonInteractive", "-Command", script], timeout=2)
        result = {"status": "unavailable", "source": "Windows local CIM and adapter counters", "captured_at": timestamp(), "cpu": None, "interfaces": [],
                  "note": "CPU load is an OS snapshot, not per-iPerf utilization. Adapters may include traffic unrelated to this test."}
        if captured["status"] == "completed" and captured["exit_code"] == 0:
            try:
                data = json.loads(captured["output"])
                loads = [float(value) for value in data["cpu"] if value is not None and 0 <= float(value) <= 100]
                interfaces = data["interfaces"]
                if not isinstance(interfaces, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in interfaces):
                    raise ValueError("Invalid adapter counters")
                result.update(status="captured" if loads else "partial", cpu={"load_percent": statistics.mean(loads), "source": "local OS load snapshot"} if loads else None,
                              interfaces=interfaces[:128])
            except (ValueError, TypeError, KeyError):
                result["note"] = "Windows counters returned unparseable or incomplete data."
        else:
            result.update(status=captured["status"] if captured["status"] != "completed" else "unavailable",
                          note="Local Windows counters could not be captured within the bounded read-only probe.")
        return result

    def start(self):
        if self.started is not None or self.result is not None or self._cancelled():
            return
        self.started = time.monotonic()
        if self.settings.get("latency") and not self._cancelled():
            self.worker = threading.Thread(target=self._loaded, name="flowbench-latency", daemon=True)
            self.worker.start()

    def _loaded(self):
        for _ in range(MAX_LOADED_SAMPLES):
            if self._cancelled():
                break
            began = time.monotonic()
            try:
                sample = self._probe("loaded")
            except Exception as exc:
                sample = {"timestamp": timestamp(), "elapsed_seconds": round(time.monotonic() - self.started, 3), "status": "error",
                          "rtt_ms": None, "rtt_upper_bound_ms": None, "note": str(exc)[:4096]}
                self._stop_children()
            self.loaded_samples.append(sample)
            if self.on_update:
                try:
                    self.on_update(self.snapshot())
                except Exception as exc:
                    self.checkpoint_error = str(exc)[:4096]
                    self._stop_children()
            if self.loaded_samples[-1]["status"] == "unavailable":
                break
            self.stop.wait(max(0, PROBE_INTERVAL - (time.monotonic() - began)))

    def cancel(self):
        self.cancel_requested = True
        self._stop_children()

    def _stop_children(self):
        self.stop.set()
        with self.lock:
            for process in tuple(self.processes):
                try:
                    process.kill()
                except OSError:
                    pass

    def _mtu(self):
        result = {"status": "disabled", "target": self.settings.get("target"), "tested_packet_sizes": [], "largest_confirmed_packet_bytes": None,
                  "note": "Same-target echo packets with fragmentation disabled. This is a bounded size check, not complete path MTU discovery or proof of a path bottleneck. Missing replies may mean ICMP is blocked."}
        if not self.settings.get("mtu"):
            return result
        if self.cancelled():
            result["status"] = "cancelled"
            return result
        if not self._ping_command(1500):
            result["status"] = "unavailable"
            result["note"] += " The native ping tool or supported selected target is unavailable; Windows IPv6 does not support this fragmentation-disabled adapter."
            return result
        result["status"] = "unconfirmed"
        deadline = time.monotonic() + 4.5
        for size in (1500, 1400, 1280):
            if self._cancelled() or time.monotonic() >= deadline:
                result["status"] = "cancelled" if self._cancelled() else "partial"
                break
            sample = dict(self._probe("path", size))
            sample["packet_bytes"] = size
            result["tested_packet_sizes"].append(sample)
            if sample["status"] == "reply":
                result.update(status="confirmed", largest_confirmed_packet_bytes=size)
                break
        if result["status"] == "unconfirmed":
            observed = phase_status(result["tested_packet_sizes"])
            if observed in {"permission_blocked", "unavailable", "timeout", "error"}:
                result["status"] = observed
        return result

    def finish(self):
        if self.result is not None:
            return self.result
        was_cancelled = self.cancel_requested or bool(self.cancelled())
        self._stop_children()
        if self.worker:
            self.worker.join(timeout=1.6)
        # Loaded probes must stop before after-traffic checks. Keep external cancellation
        # live while allowing these sequential checks after an ordinary completion.
        worker_alive = bool(self.worker and self.worker.is_alive())
        if not was_cancelled and not worker_alive:
            self.stop.clear()
        after = self._health() if self.settings.get("endpoint_health") else None
        mtu = self._mtu()
        self.stop.set()
        before = {"status": self.baseline_status, "samples": list(self.baseline_samples), "summary": latency_summary(self.baseline_samples)}
        loaded_summary = latency_summary(self.loaded_samples)
        baseline_mean, loaded_mean = before["summary"]["mean_ms"], loaded_summary["mean_ms"]
        self.result = {"schema_version": 1,
            "latency": {"enabled": bool(self.settings.get("latency")), "metric": "ICMP echo RTT", "target": self.settings.get("target"),
                        "baseline": before, "loaded": {"samples": list(self.loaded_samples), "summary": loaded_summary,
                        "status": "disabled" if not self.settings.get("latency") else "cancelled" if was_cancelled else "partial" if worker_alive else phase_status(self.loaded_samples),
                        "sample_limit_reached": len(self.loaded_samples) >= MAX_LOADED_SAMPLES},
                        "mean_increase_ms": loaded_mean - baseline_mean if loaded_mean is not None and baseline_mean is not None else None,
                        "note": "Baseline is measured before this run; other traffic may be present. Loaded RTT includes ICMP scheduling and may differ from application latency. Missing or bounded replies are not invented numeric measurements.",
                        "sample_limit": MAX_LOADED_SAMPLES, "interval_seconds": PROBE_INTERVAL},
            "endpoint_health": {"enabled": bool(self.settings.get("endpoint_health")), "local": {"provenance": "detected local", "before": self.before, "after": after,
                                "delta": health_delta(self.before, after) if self.before and after else None},
                                "remote": {"status": "unavailable", "provenance": "unavailable", "note": "A plain iPerf peer does not expose CPU or interface counters; no remote access was attempted."}},
            "path_context": {"target": self.settings.get("target"), "bind": self.settings.get("bind", ""), "requested_ip_version": self.settings.get("ip_version", "auto"),
                             "note": "Echo probes use the local OS default source address; an iPerf bind address is recorded but is not imposed on these probes. Hostnames use IPv4 probes unless IPv6 is selected; the iPerf connection can resolve differently.",
                             "probe_ip_version": self._family() if self.target else None, "port": self.settings.get("port"), "protocol": self.settings.get("protocol"),
                             "mtu": mtu, "route": {"status": "separate", "note": "New outgoing tests store one mandatory route capture before traffic alongside diagnostics. Inspect the saved route's phase; historical captures can have different timing. A probe route does not prove a throughput bottleneck."}}
        }
        if self.checkpoint_error:
            # Keep top-level schema stable; the persistence error belongs to the
            # existing endpoint evidence and is also handled by the run owner.
            self.result["endpoint_health"]["checkpoint_error"] = self.checkpoint_error
        return self.result

    def snapshot(self):
        """Portable in-progress evidence for persistence; never launches probes."""
        empty = DiagnosticsCapture({**self.settings, "latency": False, "endpoint_health": False, "mtu": False})
        result = empty.finish()
        result["latency"].update(enabled=bool(self.settings.get("latency")), note="In-progress diagnostic checkpoint; missing future samples have not been inferred.")
        for phase, samples in (("baseline", list(self.baseline_samples)), ("loaded", list(self.loaded_samples))):
            result["latency"][phase].update(status=self.baseline_status if phase == "baseline" else "running" if self.settings.get("latency") and self.started is not None else "pending" if self.settings.get("latency") else "disabled",
                                           samples=samples, summary=latency_summary(samples))
        result["endpoint_health"].update(enabled=bool(self.settings.get("endpoint_health")))
        result["endpoint_health"]["local"]["before"] = self.before
        result["path_context"]["mtu"]["status"] = "pending" if self.settings.get("mtu") else "disabled"
        return result

    def failure(self, error):
        """Retain canonical portable evidence when a diagnostic adapter fails.

        The iPerf owner can use this result without failing the traffic session.
        No further health, ping or route checks run from this recovery path.
        """
        self._stop_children()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=1.6)
        empty = DiagnosticsCapture({**self.settings, "latency": False, "endpoint_health": False, "mtu": False})
        result = empty.finish()
        reason = str(error)[:4096]
        result["latency"].update(enabled=bool(self.settings.get("latency")), note="Diagnostic capture failed; throughput evidence is retained.", error=reason)
        for phase, samples in (("baseline", self.baseline_samples), ("loaded", self.loaded_samples)):
            result["latency"][phase].update(status="error" if self.settings.get("latency") else "disabled", samples=list(samples), summary=latency_summary(samples))
        result["endpoint_health"].update(enabled=bool(self.settings.get("endpoint_health")))
        result["endpoint_health"]["local"].update(before=self.before, status="error", error=reason)
        result["path_context"]["mtu"].update(status="error" if self.settings.get("mtu") else "disabled", error=reason)
        self.result = result
        return result
