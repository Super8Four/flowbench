"""Validate saved evidence before adding it to the archive; never run imports."""

import base64
import copy
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from xml.etree import ElementTree as ET

from .metrics import effective_settings, measurements, series
from .runner import TERMINAL, now, settings_from

MAX_IMPORT_BYTES = 20 * 1024 * 1024


def parse_document(content):
    if not isinstance(content, bytes) or not content or len(content) > MAX_IMPORT_BYTES:
        raise ValueError("Choose a JSON or XML file up to 20 MiB.")
    try:
        source = content.decode("utf-8-sig")
        if source.lstrip().startswith("<"):
            if "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
                raise ValueError("XML document types and entities are not supported.")
            root = ET.fromstring(source)
            if root.tag != "iperf_run":
                raise ValueError("Use a typed XML export from this app.")
            value = decode_xml(root)
        else:
            def invalid_constant(value):
                raise ValueError("JSON must not contain NaN or Infinity.")
            value = json.loads(source, parse_constant=invalid_constant)
        validate_tree(value)
        return value
    except (UnicodeError, ET.ParseError, json.JSONDecodeError, RecursionError, KeyError, TypeError) as exc:
        raise ValueError("Invalid UTF-8 JSON/XML file.") from exc


def decode_xml(node, depth=0):
    if depth > 60:
        raise ValueError("Import nesting exceeds 60 levels.")
    kind = node.get("type")
    children = list(node)
    if kind == "object":
        result = {}
        for child in children:
            key = child.get("name")
            if child.tag != "field" or key is None or key in result:
                raise ValueError("XML object fields must have unique names.")
            result[key] = decode_xml(child, depth + 1)
        return result
    if kind == "array":
        if any(child.tag != "item" for child in children):
            raise ValueError("Invalid XML array.")
        return [decode_xml(child, depth + 1) for child in children]
    if children:
        raise ValueError("Invalid XML scalar.")
    value = node.text or ""
    if kind == "null":
        return None
    if kind == "boolean" and value in {"true", "false"}:
        return value == "true"
    if kind == "number":
        number = json.loads(value)
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError("Invalid XML number.")
        return number
    if kind == "string":
        if node.get("encoding") == "base64":
            try:
                return base64.b64decode(value, validate=True).decode("utf-8")
            except (ValueError, UnicodeError):
                raise ValueError("Invalid base64 XML string.") from None
        if node.get("encoding"):
            raise ValueError("Unsupported XML string encoding.")
        return value
    raise ValueError("Unsupported XML value type.")


def validate_tree(value, depth=0):
    if depth > 60:
        raise ValueError("Import nesting exceeds 60 levels.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Measurements must be finite numbers.")
    if isinstance(value, str):
        value.encode("utf-8")
    if isinstance(value, dict):
        for key, child in value.items():
            key.encode("utf-8")
            validate_tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            validate_tree(child, depth + 1)


def timestamp(value, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or len(value) > 100:
        raise ValueError("Saved timestamps must be ISO 8601 strings.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError("Invalid saved timestamp.") from None


def validate_measurement_structure(record):
    numeric = {"bits_per_second", "jitter_ms", "lost_percent", "lost_packets", "packets", "retransmits",
               "num_streams", "duration", "target_bitrate", "reverse", "bidir"}
    objects = {"sum", "sum_received", "sum_sent", "sum_bidir_reverse", "sum_received_bidir_reverse",
               "sum_sent_bidir_reverse", "text_summaries"}
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in numeric and (isinstance(child, bool) or not isinstance(child, (int, float)) or child < 0):
                    raise ValueError(f"Measurement {key} must be a non-negative number.")
                if key == "protocol" and (not isinstance(child, str) or child.lower() not in {"tcp", "udp"}):
                    raise ValueError("Measurement protocol must be TCP or UDP.")
                if key in objects and not isinstance(child, dict):
                    raise ValueError(f"Measurement {key} must be an object.")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for key in ("summary", "test_parameters", "intervals"):
        walk(record.get(key))
    try:
        metrics = measurements(record)
        points = series(record)
        effective_settings(record)
        validate_tree(metrics)
        validate_tree(points)
        for point in points:
            if isinstance(point["seconds"], bool) or not isinstance(point["seconds"], (int, float)):
                raise ValueError("Interval end must be a number.")
    except (TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError("Invalid measurement or interval structure.") from None


def prepare_run(data, filename):
    if not isinstance(data, dict):
        raise ValueError("Upload a saved run, comparison export, or native iperf3 -J object.")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    source_id = data.get("id")
    if "settings" in data:
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported run schema version.")
        record = copy.deepcopy(data)
        record["settings"] = settings_from(data["settings"])
        for key in ("metrics", "effective_settings", "series", "peer", "raw_stdout", "raw_stderr", "events"):
            record.pop(key, None)
        record["created_at"] = timestamp(data.get("created_at"))
        record["finished_at"] = timestamp(data.get("finished_at"), optional=True)
        if data.get("status") not in TERMINAL | {"starting", "running"}:
            raise ValueError("Invalid saved run status.")
        if data["status"] not in TERMINAL:
            record.update(status="interrupted", finished_at=record["finished_at"] or record["created_at"],
                          error="Imported snapshot of an unfinished session.")
        elif record["finished_at"] is None:
            raise ValueError("A finished run must contain its finish timestamp.")
        events = data.get("events", [])
        if not isinstance(events, list):
            raise ValueError("Saved events must be an array.")
        for index, event in enumerate(events, 1):
            if not isinstance(event, dict) or event.get("sequence") != index or isinstance(event.get("sequence"), bool):
                raise ValueError("Event sequences must start at 1 without gaps.")
            if event.get("source") not in {"system", "stdout", "stderr"} or not isinstance(event.get("message"), str):
                raise ValueError("Invalid saved event source or message.")
            timestamp(event.get("timestamp"))
        if not events:
            # Also accept a run.json backup, retaining separately supplied raw logs.
            for field, source in (("raw_stdout", "stdout"), ("raw_stderr", "stderr")):
                raw = data.get(field, "")
                if not isinstance(raw, str):
                    raise ValueError("Raw output must be text.")
                for line in raw.splitlines():
                    events.append({"sequence": len(events) + 1, "timestamp": record["created_at"], "source": source, "message": line})
    elif isinstance(data.get("start"), dict) and isinstance(data.get("end"), dict):
        start = data["start"]
        params = start.get("test_start")
        if not isinstance(params, dict):
            raise ValueError("Native iPerf JSON must contain start.test_start.")
        connection = start.get("connecting_to") or {}
        connected = start.get("connected") or []
        role = "client" if connection else "server"
        target = connection.get("host") or (connected[0].get("remote_host") if connected else "")
        port = connection.get("port") or (connected[0].get("local_port") if connected else 5201)
        # Only safe rerun defaults; exact peer parameters stay in test_parameters.
        duration = params.get("duration", 10)
        duration = max(1, min(3600, math.ceil(duration))) if isinstance(duration, (int, float)) else 10
        settings = settings_from({"role": role, "target": target, "port": port,
                                  "duration": duration, "streams": params.get("num_streams", 1),
                                  "protocol": str(params.get("protocol", "tcp")).lower(),
                                  "mode": "bidir" if params.get("bidir") else "download" if params.get("reverse") else "upload",
                                  "label": filename[:200]})
        seconds = (start.get("timestamp") or {}).get("timesecs")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError("Native JSON must include start.timestamp.timesecs to retain the original test date.")
        try:
            created = datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError("Invalid native iPerf timestamp.") from None
        error = str(data["error"]) if data.get("error") else None
        record = {"schema_version": 1, "settings": settings, "created_at": created.isoformat(),
                  "finished_at": (created + timedelta(seconds=duration)).isoformat(),
                  "status": "failed" if error else "completed", "exit_code": None if error else 0,
                  "error": error, "iperf_version": start.get("version"), "command": [],
                  "output_format": "json", "summary": data["end"], "intervals": data.get("intervals", []),
                  "native_result": data, "test_parameters": params}
        events = [{"sequence": 1, "timestamp": record["created_at"], "source": "stdout",
                   "message": json.dumps(data, indent=2, ensure_ascii=False)}]
    else:
        raise ValueError("File is not a saved run or native iperf3 -J result.")
    if not isinstance(record.get("summary"), dict) or not isinstance(record.get("intervals"), list):
        raise ValueError("Saved summary must be an object and intervals an array.")
    if record.get("test_parameters") is not None and not isinstance(record["test_parameters"], dict):
        raise ValueError("Saved test parameters must be an object.")
    if not isinstance(record.get("command"), list) or any(not isinstance(arg, str) for arg in record["command"]):
        raise ValueError("Saved command must be an array of strings.")
    if record.get("iperf_version") is not None and not isinstance(record["iperf_version"], str):
        raise ValueError("iPerf version must be text.")
    if record.get("error") is not None and not isinstance(record["error"], str):
        raise ValueError("Saved error must be text.")
    code = record.get("exit_code")
    if code is not None and (isinstance(code, bool) or not isinstance(code, int)):
        raise ValueError("Saved exit code must be a number or null.")
    validate_measurement_structure(record)
    record.update(id=uuid4().hex, event_count=len(events),
                  imported={"at": now(), "filename": filename[:255], "source_id": source_id,
                            "sha256": digest, "format": "app" if "settings" in data else "native-iperf"})
    return record, events


def import_runs(manager, data, filename):
    sources = [data]
    if isinstance(data, dict) and "before" in data and "after" in data:
        sources = [data["before"], data["after"]]
    try:
        prepared = [prepare_run(source, filename) for source in sources]
    except (TypeError, AttributeError, OverflowError, RecursionError):
        raise ValueError("Invalid saved test structure.") from None
    imported, duplicates = [], []
    with manager.lock:
        if manager.storage_error:
            raise RuntimeError("Restore archive storage access before importing tests.")
        existing = {}
        for path in manager.directory.glob("*/run.json"):
            record = manager.read(path.parent.name)
            digest = (record.get("imported") or {}).get("sha256")
            if digest:
                existing[digest] = record["id"]
        for record, events in prepared:
            digest = record["imported"]["sha256"]
            if digest in existing:
                duplicates.append(existing[digest])
                continue
            # Stage below an extra directory level; history/recovery only see
            # a fully written run after the atomic directory rename.
            stage = manager.directory / ".imports" / record["id"]
            stage.mkdir(parents=True)
            try:
                (stage / "events.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
                (stage / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                stage.replace(manager.path(record["id"]))
            finally:
                if stage.exists():
                    for name in ("events.jsonl", "run.json"):
                        (stage / name).unlink(missing_ok=True)
                    stage.rmdir()
            existing[digest] = record["id"]
            imported.append(record["id"])
    return {"imported": imported, "duplicates": duplicates}
