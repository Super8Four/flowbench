"""Bounded, unprivileged route evidence; independent of iPerf measurements."""
import os
from datetime import datetime, timezone
import ipaddress
import math
import re
import shutil
import subprocess
import time


def validate_route(value):
    # Keep the public route validation entry point beside capture/parsing while
    # sharing timing/schema validation with the preflight envelope.
    from .preflight import validate_route as validate
    return validate(value)


def route_command(target, ip_version="auto"):
    if os.name == "nt":
        tool = shutil.which("tracert")
        return [tool, "-d", "-h", "16", "-w", "300", *(["-" + ip_version] if ip_version != "auto" else []), target] if tool else None
    tool = shutil.which("tracepath")
    return [tool, "-n", "-m", "16", *(["-" + ip_version] if ip_version != "auto" else []), target] if tool else None


def parse_hops(output):
    """Group observed replies by TTL, preserving alternate addresses and bounds."""
    hops = {}
    for line in output.splitlines():
        match = re.match(r"\s*(\d+)(?:\?:|:)?\s+(.+)", line)
        if not match or not 1 <= int(match[1]) <= 16:
            continue
        detail = match[2].strip()
        if detail.startswith("[LOCALHOST]"):
            continue
        addresses = []
        for token in detail.split():
            candidate = token.strip("[](),;")
            try:
                address = str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
            if address not in addresses:
                addresses.append(address)
        measured = re.findall(r"(<)?\s*(\d+(?:\.\d+)?)\s*ms", detail)
        times = [float(value) for bound, value in measured if not bound and math.isfinite(float(value))][:16]
        upper_bounds = [float(value) for bound, value in measured if bound and math.isfinite(float(value))][:16]
        number = int(match[1])
        hop = hops.setdefault(number, {"hop": number, "address": None, "addresses": [], "rtt_ms": [],
                                      "rtt_upper_bound_ms": [], "responded": False, "observed": True, "detail": "", "observations": []})
        for address in addresses:
            if address not in hop["addresses"] and len(hop["addresses"]) < 16:
                hop["addresses"].append(address)
        hop["address"] = hop["addresses"][0] if hop["addresses"] else None
        hop["responded"] = bool(hop["addresses"])
        hop["rtt_ms"] = (hop["rtt_ms"] + times)[:16]
        hop["rtt_upper_bound_ms"] = (hop["rtt_upper_bound_ms"] + upper_bounds)[:16]
        hop["detail"] = (hop["detail"] + ("\n" if hop["detail"] else "") + detail)[:4096]
        if len(hop["observations"]) < 16:
            hop["observations"].append({"addresses": addresses[:16], "rtt_ms": times,
                                         "rtt_upper_bound_ms": upper_bounds, "detail": detail[:2048]})
    return [hops.get(number, {"hop": number, "address": None, "addresses": [], "rtt_ms": [], "rtt_upper_bound_ms": [],
                              "responded": False, "observed": False, "detail": "No observation captured for this TTL.", "observations": []})
            for number in range(1, max(hops, default=0) + 1)]


def capture_route(settings, on_start, cancelled, timeout=20):
    started = time.monotonic()
    command = route_command(settings["target"], settings.get("ip_version", "auto"))
    result = {"status": "unavailable", "target": settings["target"], "command": command or [],
              "phase": "before_traffic", "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None, "elapsed_seconds": None,
              "hops": [], "raw_output": "", "output_truncated": False, "note": "Route tool unavailable; the test can continue without route evidence."}
    process = None
    try:
        if cancelled():
            result.update(status="cancelled", note="Route capture cancelled before launch.")
            return result
        if not command:
            return result
        process = on_start(command)
        if process is None:
            result.update(status="cancelled", note="Route capture cancelled.")
            return result
        deadline = started + min(20, max(.1, timeout))
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
        encoded_output = output.encode("utf-8", errors="replace")
        result["output_truncated"] = len(encoded_output) > 32768
        output = encoded_output[-32768:].decode("utf-8", errors="ignore")
        if cancelled():
            status = "cancelled"
        hops = parse_hops(output)
        if status == "captured" and not hops:
            status = "incomplete"
        result.update(status=status, raw_output=output, hops=hops, exit_code=process.returncode,
                      note="One route capture before this outgoing test. Missing replies do not prove packet loss; TTL ordering is not a geographic map or proof of a bottleneck. The probe path/source can differ from the traffic connection.")
        if result["output_truncated"]:
            result["note"] += " Native output exceeded 32 KiB; only its trailing bounded output was retained."
    except OSError as exc:
        result.update(status="permission_blocked" if isinstance(exc, PermissionError) else "unavailable" if isinstance(exc, FileNotFoundError) else "error", note=f"Route capture failed: {exc}")
    finally:
        if process and process.poll() is None:
            process.kill()
            try:
                process.communicate(timeout=.3)
            except subprocess.TimeoutExpired:
                pass
        result.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(max(0, time.monotonic() - started), 6))
    return result
