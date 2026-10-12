"""Optional external Flent RRUL CLI; native evidence never masquerades as iPerf."""

import gzip
import json
import math
import os
from pathlib import Path
import shutil
import signal
import socket
import statistics
import subprocess
import sys

MAX_RESULT_BYTES = 20 * 1024 * 1024
WORKLOADS = {"rrul"}


def unprivileged_icmp():
    """Check existing kernel permission only; no packets or sysctl writes."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP):
            pass
        return {"available": True, "method": "Existing ICMP datagram socket permission; no packets sent."}
    except OSError as exc:
        return {"available": False, "error": "The kernel does not permit an unprivileged ICMP datagram socket for this process: " + str(exc)}


def capabilities():
    dependencies = {name: shutil.which(name) for name in ("flent", "netperf", "ping", "fping")}
    result = {"available": False, "version": None, "netperf_version": None, "unprivileged_icmp": None,
              "platform": sys.platform, "dependencies": dependencies,
              "workloads": ["rrul"], "max_duration": 60,
              "note": "RRUL uses eight TCP load flows plus UDP/ICMP RTT. Requires an owned, ready Netserver target; tools are optional external installs."}
    if not sys.platform.startswith("linux"):
        result["error"] = "Actual Flent RRUL execution is supported on Linux only; native Windows is unavailable."
        return result
    if not dependencies["flent"] or not dependencies["netperf"] or not (dependencies["ping"] or dependencies["fping"]):
        result["error"] = "Install optional Flent, Netperf with demo support and timestamped ping/fping locally, then restart FlowBench."
        return result
    try:
        result["unprivileged_icmp"] = unprivileged_icmp()
        if not result["unprivileged_icmp"]["available"]:
            result["error"] = result["unprivileged_icmp"]["error"]
            return result
        # Verify exec permission without a destination or any network traffic.
        # A file capability masked by cap-drop may cause execve itself to fail.
        ping_tool = dependencies["fping"] or dependencies["ping"]
        ping_version = subprocess.run([ping_tool, "-v" if dependencies["fping"] else "-V"],
                                      capture_output=True, text=True, timeout=5)
        if ping_version.returncode != 0:
            raise ValueError("The detected ping tool cannot execute its version check under the current permissions.")
        info = subprocess.run([dependencies["flent"], "--version"], capture_output=True, text=True, timeout=5)
        version = (info.stdout + info.stderr).strip()
        if info.returncode != 0 or "flent" not in version.lower():
            raise ValueError("The detected Flent CLI did not identify itself successfully.")
        netperf = subprocess.run([dependencies["netperf"], "-V"], capture_output=True, text=True, timeout=5)
        netperf_version = (netperf.stdout + netperf.stderr).strip()
        if netperf.returncode != 0 or "netperf" not in netperf_version.lower():
            raise ValueError("The detected Netperf CLI did not identify itself successfully.")
        result.update(available=True, version=version[:500], netperf_version=netperf_version[:500])
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["error"] = str(exc)
    return result


def settings(data):
    from .runner import settings_from
    if not isinstance(data, dict) or not isinstance(data.get("workload", "rrul"), str) or data.get("workload", "rrul") not in WORKLOADS:
        raise ValueError("Choose the supported Flent rrul workload.")
    if data.get("confirm_netserver") is not True:
        raise ValueError("Confirm that this is an owned, ready Netserver and that eight TCP load flows are intended.")
    duration = data.get("duration", 10)
    port = data.get("netserver_port", 12865)
    result = settings_from(dict(data, role="client", protocol="tcp", mode="bidir", streams=4,
                                duration=duration, port=port, bandwidth="0", traceroute=False))
    if result["duration"] > 60:
        raise ValueError("Flent workload duration is limited to 60 seconds.")
    if result.get("bind") or any(result.get(key) for key in ("latency", "endpoint_health", "mtu")):
        raise ValueError("Flent captures its own native evidence; separate iPerf bind and diagnostic options are not accepted here.")
    result["flent_workload"] = "rrul"
    return result


def command(info, settings, directory):
    directory = Path(directory).resolve()
    rcfile = directory / "empty.flentrc"
    rcfile.write_text("", encoding="utf-8")
    result = [info["dependencies"]["flent"], "rrul", "-H", settings["target"], "-l", str(settings["duration"]),
              "-d", "2", "-s", "1", "--netperf-control-port", str(settings["port"]),
              "-r", str(rcfile), "-D", str(directory)]
    if settings.get("ip_version", "auto") != "auto":
        result.append("-" + settings["ip_version"])
    return result


def stop_process(process, force=False):
    """The worker starts a fresh session; signal only that owned process group."""
    if not process:
        return
    try:
        if sys.platform.startswith("linux"):
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        pass


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate_flent(value):
    if not isinstance(value, dict) or value.get("tool") != "Flent" or value.get("workload") != "rrul":
        raise ValueError("Invalid Flent provenance/workload.")
    if value.get("status") not in {"captured", "partial", "missing", "failed", "cancelled"}:
        raise ValueError("Invalid Flent evidence status.")
    data = value.get("data")
    if data is None:
        if value.get("series") or value.get("summaries"):
            raise ValueError("Flent summaries require native source data.")
        return value
    parsed = normalize(data, value.get("version"))
    for key in ("series", "summaries"):
        if value.get(key) != parsed[key]:
            raise ValueError("Flent derived evidence does not match its native result.")
    return value


def normalize(data, version=None):
    if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict) or data["metadata"].get("NAME") != "rrul":
        raise ValueError("Expected a native Flent rrul result.")
    x, results = data.get("x_values"), data.get("results")
    if not isinstance(x, list) or len(x) > 5000 or not isinstance(results, dict) or not 1 <= len(results) <= 100:
        raise ValueError("Invalid or oversized Flent time series.")
    if any(not _number(v) or v < 0 for v in x) or any(b < a for a, b in zip(x, x[1:])):
        raise ValueError("Flent elapsed time must contain finite ordered nonnegative seconds.")
    from .imports import validate_tree
    validate_tree(data)
    series, summaries = [], []
    for name, values in results.items():
        if not isinstance(name, str) or len(name) > 200 or not isinstance(values, list) or len(values) != len(x):
            raise ValueError("Flent series must align with native elapsed time.")
        if any(value is not None and not _number(value) for value in values):
            raise ValueError("Flent values must be finite numbers or missing null values.")
        # These units are defined by the official RRUL workload, not inferred
        # from magnitude. Unknown native series retain unknown units.
        unit = "Mbits/s" if name.startswith("TCP upload") or name.startswith("TCP download") else "ms" if name.startswith("Ping (ms)") else None
        present = [v for v in values if v is not None]
        series.append({"name": name, "unit": unit, "points": [{"seconds": t, "value": value} for t, value in zip(x, values)]})
        summaries.append({"name": name, "unit": unit, "count": len(present), "missing": len(values) - len(present),
                          "mean": statistics.mean(present) if present else None, "min": min(present) if present else None,
                          "max": max(present) if present else None})
    return {"tool": "Flent", "version": version, "workload": "rrul", "status": "captured", "data": data,
            "series": series, "summaries": summaries,
            "provenance": "External Flent CLI native RRUL JSON; TCP rates and UDP/ICMP RTT retain their native series identities. RTT is not application latency."}


def read_result(directory, version=None):
    directory = Path(directory).resolve()
    files = [p for p in directory.iterdir() if p.name.endswith((".flent.gz", ".flent", ".json.gz", ".json"))]
    if len(files) != 1:
        raise ValueError("Flent did not produce exactly one native result file; raw logs remain available.")
    path = files[0]
    if path.is_symlink() or path.resolve().parent != directory:
        raise ValueError("Flent result must stay inside its owned output directory.")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        content = handle.read(MAX_RESULT_BYTES + 1)
    if len(content) > MAX_RESULT_BYTES:
        raise ValueError("Flent native result exceeds 20 MiB.")
    data = json.loads(content, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite Flent JSON")))
    return normalize(data, version)
