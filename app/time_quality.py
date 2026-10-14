"""Bounded observations of existing local time services; never set the clock.

chronyc CSV reports seconds and disables DNS. Its System time field is the
signed remaining correction (daemon NTP clock minus system clock), not Last
offset. Windows Phase Offset remains the provider-reported signed correction.
Root dispersion is reported as a proxy, never an independently verified bound.
"""

import csv
from datetime import datetime, timedelta, timezone
import io
import math
import os
from pathlib import PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import threading
import time


MAX_OUTPUT = 8192
STATUSES = {"pending", "captured", "unsynchronized", "status_only", "unavailable",
            "permission_blocked", "malformed", "timeout", "cancelled", "error"}
PROVIDERS = {None, "chrony", "windows_time", "systemd"}
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
DISCLAIMER = "Local service observation only; timestamps use system UTC and are not independently NTP verified."
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def time_quality_result(status="unavailable", note=None):
    """Canonical pending/error payload for callers; this performs no query."""
    if status not in STATUSES:
        raise ValueError("Unsupported time-quality status.")
    if note is not None and not isinstance(note, str):
        raise ValueError("Time-quality notes must be text.")
    return {"schema_version": 1, "status": status, "observed_at": _utc_now(),
            "clock_source": "system_utc", "ntp_verified": False, "provider": None,
            "source": None, "synchronized": None, "offset_seconds": None,
            "uncertainty_seconds": None, "uncertainty_kind": None,
            "root_delay_seconds": None, "root_dispersion_seconds": None,
            "last_sync_at": None, "query_elapsed_seconds": 0.0,
            "note": ((note[:1800] + " ") if note else "") + DISCLAIMER,
            "raw_output": "", "command": []}


FIELDS = set(time_quality_result())


def _finite(value, nonnegative=False):
    try:
        return (type(value) in (int, float) and math.isfinite(value)
                and (not nonnegative or value >= 0))
    except OverflowError:
        return False


def _iso_utc(value):
    if not isinstance(value, str) or len(value) > 40 or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value
    ):
        raise ValueError("Time-quality timestamps must be explicit ISO UTC.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid time-quality UTC timestamp.") from None


def validate_time_quality(value):
    """Validate imported metadata without running any stored command."""
    if not isinstance(value, dict) or set(value) != FIELDS or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported time-quality schema or fields.")
    if not isinstance(value["status"], str) or value["status"] not in STATUSES:
        raise ValueError("Invalid time-quality status.")
    if value["clock_source"] != "system_utc" or value["ntp_verified"] is not False:
        raise ValueError("Local time-quality observations cannot claim independent NTP verification.")
    if not isinstance(value["provider"], (str, type(None))) or value["provider"] not in PROVIDERS:
        raise ValueError("Invalid time-quality provider.")
    _iso_utc(value["observed_at"])
    if value["last_sync_at"] is not None:
        _iso_utc(value["last_sync_at"])
    if value["synchronized"] is not None and type(value["synchronized"]) is not bool:
        raise ValueError("Invalid reported synchronization state.")
    for key in ("offset_seconds", "uncertainty_seconds", "root_delay_seconds", "root_dispersion_seconds"):
        if value[key] is not None and not _finite(value[key], key in {"uncertainty_seconds", "root_dispersion_seconds"}):
            raise ValueError("Time-quality measurements must be finite, bounded numeric values.")
    if not _finite(value["query_elapsed_seconds"], True) or value["query_elapsed_seconds"] > 10:
        raise ValueError("Invalid time-quality query duration.")
    for key, limit, nullable in (("source", 512, True), ("note", 2048, False), ("raw_output", MAX_OUTPUT, False)):
        text = value[key]
        if text is None and nullable:
            continue
        if not isinstance(text, str) or len(text) > limit:
            raise ValueError("Invalid or oversized time-quality text.")
        if key == "source" and any(ord(character) < 32 or ord(character) == 127 for character in text):
            raise ValueError("Time-source identifiers cannot contain control characters.")
    if value["uncertainty_kind"] not in (None, "root_dispersion_proxy"):
        raise ValueError("Unsupported time uncertainty interpretation.")
    if (value["uncertainty_seconds"] is None) != (value["uncertainty_kind"] is None):
        raise ValueError("An uncertainty proxy must identify its interpretation.")
    if value["uncertainty_seconds"] is not None and (value["root_dispersion_seconds"] is None
            or value["uncertainty_seconds"] != value["root_dispersion_seconds"] or value["synchronized"] is not True):
        raise ValueError("Time uncertainty must be the synchronized provider's reported root-dispersion proxy.")
    command = value["command"]
    if not isinstance(command, list) or len(command) > 7 or any(not isinstance(arg, str) or not arg or len(arg) > 1024 or "\x00" in arg for arg in command):
        raise ValueError("Invalid stored local time query.")
    if command:
        executable = PurePosixPath(command[0].replace("\\", "/")).name.lower()
        expected = {"chrony": ({"chronyc"}, ["-n", "-c", "-h", "127.0.0.1", "tracking"]),
                    "windows_time": ({"w32tm", "w32tm.exe"}, ["/query", "/status", "/verbose"]),
                    "systemd": ({"timedatectl"}, ["show", "--property=NTPSynchronized"])}.get(value["provider"])
        if expected is None or executable not in expected[0] or command[1:] != expected[1]:
            raise ValueError("Only fixed local read-only time query metadata is supported.")
    elif value["provider"] is not None:
        raise ValueError("A local time provider observation must identify its read-only command.")
    if value["provider"] == "systemd" and any(value[key] is not None for key in
            ("source", "offset_seconds", "uncertainty_seconds", "root_delay_seconds", "root_dispersion_seconds", "last_sync_at")):
        raise ValueError("The systemd synchronization flag cannot establish time offsets or source details.")
    if value["status"] == "unsynchronized" and value["synchronized"] is not False:
        raise ValueError("Unsynchronized evidence must retain the provider's false state.")
    if value["status"] in {"captured", "status_only"} and value["synchronized"] is not True:
        raise ValueError("A successful time query needs the provider's true synchronization state.")
    if value["status"] == "captured" and value["provider"] not in {"chrony", "windows_time"}:
        raise ValueError("Captured time details require a supported detailed provider.")
    if value["status"] == "status_only" and value["provider"] != "systemd":
        raise ValueError("Only the systemd flag query supplies status-only time evidence.")
    if value["offset_seconds"] is not None and value["synchronized"] is not True:
        raise ValueError("An unsynchronized service cannot provide a current synchronized offset.")
    if value["status"] in {"pending", "unavailable", "permission_blocked", "malformed", "timeout", "cancelled", "error"} and (
            value["synchronized"] is not None or any(value[key] is not None for key in
            ("source", "offset_seconds", "uncertainty_seconds", "root_delay_seconds", "root_dispersion_seconds", "last_sync_at"))):
        raise ValueError("A missing time observation cannot contain captured measurements.")
    if value["provider"] is None and (command or value["synchronized"] is not None or any(value[key] is not None for key in
            ("source", "offset_seconds", "uncertainty_seconds", "root_delay_seconds", "root_dispersion_seconds", "last_sync_at"))):
        raise ValueError("Time measurements require a local provider.")
    return value


def _number(text, nonnegative=False):
    if not isinstance(text, str) or len(text) > 80 or not re.fullmatch(NUMBER, text.strip()):
        raise ValueError("Unsupported numeric time-service output.")
    result = float(text)
    if not _finite(result, nonnegative):
        raise ValueError("Nonfinite or invalid time-service measurement.")
    return result


def _chrony(raw, result):
    rows = list(csv.reader(io.StringIO(raw.strip())))
    if len(rows) != 1 or len(rows[0]) != 14:
        raise ValueError("Expected chrony tracking CSV with 14 fields.")
    row = [item.strip() for item in rows[0]]
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", row[0]) or not row[2].isdigit() or not 0 <= int(row[2]) <= 16:
        raise ValueError("Invalid chrony reference/stratum.")
    if len(row[1]) > 512 or not row[1] or any(c.isspace() for c in row[1]):
        raise ValueError("Invalid chrony source identifier.")
    leap = row[13]
    if leap not in {"Normal", "Insert second", "Delete second", "Not synchronised"}:
        raise ValueError("Unsupported chrony leap status.")
    numbers = [_number(item) for item in row[3:13]]
    if numbers[0] < 0 or numbers[3] < 0 or numbers[6] < 0 or numbers[8] < 0 or numbers[9] < 0:
        raise ValueError("Invalid chrony reference time, RMS, dispersion or interval.")
    synchronized = leap != "Not synchronised" and 1 <= int(row[2]) <= 15
    result.update(status="captured" if synchronized else "unsynchronized", synchronized=synchronized,
                  source=row[1] if row[0] != "00000000" else None,
                  offset_seconds=numbers[1] if synchronized else None,
                  root_delay_seconds=numbers[7], root_dispersion_seconds=numbers[8],
                  uncertainty_seconds=numbers[8] if synchronized else None,
                  uncertainty_kind="root_dispersion_proxy" if synchronized else None,
                  last_sync_at=datetime.fromtimestamp(numbers[0], timezone.utc).isoformat() if numbers[0] > 0 else None)
    result["note"] = ("Signed chrony System time correction (daemon NTP clock minus system clock), not Last offset. "
                      "Root dispersion is a provider proxy, not guaranteed clock accuracy. " + DISCLAIMER)
    if row[0].upper() == "7F7F0101":
        result["note"] = "Chrony reports local reference mode, which does not establish synchronization to an external source. " + result["note"]
    return result


def _windows(raw, result):
    fields = {}
    for line in raw.splitlines():
        if ":" in line:
            key, text = line.split(":", 1)
            key = key.strip()
            if key in fields:
                raise ValueError("Duplicate Windows time status field.")
            fields[key] = text.strip()
    if not {"Leap Indicator", "Stratum", "Source"} <= fields.keys():
        raise ValueError("Windows time status is unsupported or localized; no values were guessed.")
    def integer(key):
        match = re.fullmatch(r"(\d+)(?:\s*\([^\r\n]*\))?", fields[key])
        if not match:
            raise ValueError("Malformed Windows synchronization state.")
        return int(match[1])
    leap, stratum = integer("Leap Indicator"), integer("Stratum")
    if leap > 3 or stratum > 16 or len(fields["Source"]) > 512 or not fields["Source"]:
        raise ValueError("Invalid Windows time-service state.")
    synchronized = leap != 3 and 1 <= stratum <= 15 and fields["Source"].lower() not in {"local cmos clock", "free-running system clock"}
    if "Last Sync Error" in fields and integer("Last Sync Error") != 0:
        synchronized = False
    def seconds(key, nonnegative=False):
        if key not in fields:
            return None
        match = re.fullmatch("(" + NUMBER + r")s", fields[key])
        if not match:
            raise ValueError("Unsupported Windows time unit/locale.")
        return _number(match[1], nonnegative)
    phase, delay, dispersion = seconds("Phase Offset"), seconds("Root Delay"), seconds("Root Dispersion", True)
    age = seconds("Time since Last Good Sync Time", True)
    last_sync = None
    if age is not None and synchronized:
        last_sync = (_iso_utc(result["observed_at"]) - timedelta(seconds=age)).isoformat()
    result.update(status="captured" if synchronized else "unsynchronized", synchronized=synchronized,
                  source=fields["Source"], offset_seconds=phase if synchronized else None,
                  root_delay_seconds=delay, root_dispersion_seconds=dispersion,
                  uncertainty_seconds=dispersion if synchronized else None,
                  uncertainty_kind="root_dispersion_proxy" if synchronized and dispersion is not None else None,
                  last_sync_at=last_sync)
    result["note"] = ("Windows Phase Offset is the provider-reported signed correction. Root dispersion is a proxy, not guaranteed accuracy. "
                      "Last-sync UTC is derived only from the reported elapsed age; localized calendar dates are not guessed. " + DISCLAIMER)
    return result


def _systemd(raw, result):
    text = raw.strip()
    if text not in {"NTPSynchronized=yes", "NTPSynchronized=no"}:
        raise ValueError("Unsupported systemd synchronization status.")
    result.update(synchronized=text.endswith("=yes"), status="status_only" if text.endswith("=yes") else "unsynchronized",
                  note="systemd reports only its synchronization flag; source, offset and uncertainty are unavailable. " + DISCLAIMER)
    return result


def _permission(raw):
    text = raw.lower()
    return any(token in text for token in ("access is denied", "access denied", "permission denied", "not authorised", "not authorized", "operation not permitted", "0x80070005"))


def _query(command, cancelled, deadline):
    """Own and drain one bounded local process; memory use is also bounded."""
    process, reader = None, None
    output = bytearray()
    oversized = threading.Event()
    try:
        if cancelled():
            return "cancelled", ""
        if time.monotonic() >= deadline:
            return "timeout", ""
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   shell=False, creationflags=CREATE_FLAGS, start_new_session=os.name != "nt")
        def drain():
            try:
                while True:
                    part = process.stdout.read(1024)
                    if not part:
                        break
                    remaining = MAX_OUTPUT - len(output)
                    output.extend(part[:remaining])
                    if len(part) > remaining:
                        oversized.set()
            except (OSError, ValueError):
                pass
        reader = threading.Thread(target=drain, name="flowbench-time-query", daemon=True)
        reader.start()
        status = None
        while process.poll() is None:
            if cancelled():
                status = "cancelled"
                break
            if time.monotonic() >= deadline:
                status = "timeout"
                break
            if oversized.is_set():
                status = "malformed"
                break
            try:
                process.wait(timeout=min(.05, max(.001, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        if status is not None:
            process.kill()
        process.wait(timeout=.15)
        reader.join(timeout=.05)
        raw = bytes(output).decode("utf-8", errors="replace")[:MAX_OUTPUT]
        if status is not None:
            return status, raw
        if oversized.is_set():
            return "malformed", raw
        return ("permission_blocked" if _permission(raw) else "ok" if process.returncode == 0 else "unavailable"), raw
    except PermissionError as exc:
        return "permission_blocked", str(exc)[:MAX_OUTPUT]
    except OSError as exc:
        return "unavailable", str(exc)[:MAX_OUTPUT]
    except subprocess.TimeoutExpired:
        return "timeout", bytes(output).decode("utf-8", errors="replace")[:MAX_OUTPUT]
    finally:
        if process:
            try:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                elif process.poll() is None:
                    process.kill()
                process.wait(timeout=.15)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if reader:
                reader.join(timeout=.05)
            if process.stdout:
                process.stdout.close()


def capture_time_quality(cancelled=lambda: False, timeout=3):
    if not _finite(timeout, True):
        raise ValueError("Time-quality timeout must be a finite nonnegative duration.")
    started = time.monotonic()
    # Reserve up to 0.4 seconds for kill/reap/drain within the aggregate budget.
    deadline = started + max(0.0, min(timeout, 3) - .4)
    result = time_quality_result()
    if cancelled():
        result["status"] = "cancelled"
        return result
    commands = []
    if sys.platform == "win32":
        path = shutil.which("w32tm")
        if path:
            commands.append(("windows_time", [path, "/query", "/status", "/verbose"], _windows))
    elif sys.platform.startswith("linux"):
        path = shutil.which("chronyc")
        if path:
            commands.append(("chrony", [path, "-n", "-c", "-h", "127.0.0.1", "tracking"], _chrony))
        path = shutil.which("timedatectl")
        if path:
            commands.append(("systemd", [path, "show", "--property=NTPSynchronized"], _systemd))
    if not commands:
        result["note"] = "No supported local time-service query tool is available. " + DISCLAIMER
    previous = None
    for index, (provider, command, parser) in enumerate(commands):
        result = time_quality_result()
        result.update(provider=provider, command=command)
        query_deadline = min(deadline, time.monotonic() + 1.5) if index < len(commands) - 1 else deadline
        status, raw = _query(command, cancelled, query_deadline)
        result["raw_output"] = raw
        if status == "ok":
            try:
                parser(raw, result)
                validate_time_quality(result)
            except (ValueError, OverflowError, OSError, csv.Error):
                result = time_quality_result("malformed", "The local time-service response could not be parsed safely; no measurement was guessed.")
                result.update(provider=provider, command=command, raw_output=raw)
        else:
            result["status"] = status
            result["note"] = "Local time-service query " + status.replace("_", " ") + ". " + DISCLAIMER
        if previous:
            result["note"] = "Chrony query " + previous + "; systemd fallback. " + result["note"]
        if result["status"] in {"captured", "unsynchronized", "status_only", "cancelled"}:
            break
        previous = result["status"]
        if time.monotonic() >= deadline:
            break
    result["query_elapsed_seconds"] = max(0.0, time.monotonic() - started)
    validate_time_quality(result)
    return result
