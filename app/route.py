"""Bounded, unprivileged route evidence; independent of iPerf measurements."""
import os
import re
import shutil
import subprocess
import time


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
        measured = re.findall(r"(<)?\s*(\d+(?:\.\d+)?)\s*ms", detail)
        times = [float(value) for bound, value in measured if not bound]
        upper_bounds = [float(value) for bound, value in measured if bound]
        hops.append({"hop": int(match[1]), "address": addresses[-1] if addresses else None,
                     "rtt_ms": times, "rtt_upper_bound_ms": upper_bounds, "responded": bool(addresses), "detail": detail})
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
        deadline = time.monotonic() + min(20, max(.1, timeout))
        while True:
            if cancelled() or time.monotonic() >= deadline:
                status = "cancelled" if cancelled() else "timeout"
                if process.poll() is None:
                    process.kill()
                try:
                    output, _ = process.communicate(timeout=.3)
                except subprocess.TimeoutExpired:
                    output = ""
                break
            try:
                output, _ = process.communicate(timeout=min(.1, max(.001, deadline - time.monotonic())))
                status = "captured" if process.returncode == 0 else "permission_blocked" if re.search(r"permission|not permitted|access.*denied|requires.*privilege", output, re.I) else "incomplete"
                break
            except subprocess.TimeoutExpired:
                continue
        output = output[-32768:]
        if cancelled():
            status = "cancelled"
        result.update(status=status, raw_output=output, hops=parse_hops(output), exit_code=process.returncode,
                      note="Route probes run after throughput capture. Missing replies do not prove packet loss; the probe path can differ from iPerf's path.")
    except OSError as exc:
        result.update(status="permission_blocked" if isinstance(exc, PermissionError) else "unavailable", note=f"Route capture failed: {exc}")
    finally:
        if process and process.poll() is None:
            process.kill()
            try:
                process.communicate(timeout=.3)
            except subprocess.TimeoutExpired:
                pass
    return result
