"""Bounded, unprivileged route evidence; independent of iPerf measurements."""
import os
import re
import shutil
import subprocess


def route_command(target, ip_version="auto"):
    if os.name == "nt":
        tool = shutil.which("tracert")
        return [tool, "-d", "-h", "16", "-w", "300", *(["-" + ip_version] if ip_version != "auto" else []), target] if tool else None
    tool = shutil.which("tracepath")
    return [tool, "-n", "-m", "16", *(["-" + ip_version] if ip_version != "auto" else []), target] if tool else None


def parse_hops(output):
    hops = []
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)(?:\?:|:)?\s+(.+)", line)
        if not match or not 1 <= int(match[1]) <= 16:
            continue
        detail = match[2].strip()
        if detail.startswith("[LOCALHOST]"):
            continue
        addresses = re.findall(r"(?:\d{1,3}\.){3}\d{1,3}|[\da-fA-F]*:[\da-fA-F:.]+", detail)
        times = [float(value) for value in re.findall(r"<?\s*(\d+(?:\.\d+)?)\s*ms", detail)]
        hops.append({"hop": int(match[1]), "address": addresses[-1] if addresses else None,
                     "rtt_ms": times, "responded": bool(addresses), "detail": detail})
    return hops


def capture_route(settings, on_start, cancelled, timeout=20):
    command = route_command(settings["target"], settings.get("ip_version", "auto"))
    result = {"status": "unavailable", "target": settings["target"], "command": command or [],
              "hops": [], "raw_output": "", "note": "Route tool unavailable; throughput results are retained."}
    if not command:
        return result
    process = None
    try:
        process = on_start(command)
        if process is None:
            result.update(status="cancelled", note="Route capture cancelled.")
            return result
        try:
            output, _ = process.communicate(timeout=timeout)
            status = "captured" if process.returncode == 0 else "incomplete"
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            status = "timeout"
        if cancelled():
            status = "cancelled"
        result.update(status=status, raw_output=output, hops=parse_hops(output), exit_code=process.returncode,
                      note="Route probes run after throughput capture. Missing replies do not prove packet loss; the probe path can differ from iPerf's path.")
    except OSError as exc:
        result.update(status="error", note=f"Route capture failed: {exc}")
    return result
