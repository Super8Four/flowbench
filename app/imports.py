"""Validate saved evidence before adding it to the archive; never run imports."""

import base64
import copy
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from xml.etree import ElementTree as ET

from .metrics import effective_settings, measurements, series, identity_issues
from .runner import TERMINAL, now, settings_from
from .annotations import annotations_from

MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_PREPARED_BYTES = 32 * 1024 * 1024


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
    issues = identity_issues(record)
    if issues:
        raise ValueError("Invalid saved measurement identity. " + " ".join(issues))
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


def prepare_run(data, filename, allow_job=False):
    if not isinstance(data, dict):
        raise ValueError("Upload a saved run, comparison export, or native iperf3 -J object.")
    if data.get("kind") in {"protocol-job", "matrix-job"} and not allow_job:
        raise ValueError("A job must be imported as a complete export with its child tests.")
    if data.get("kind") == "matrix-job":
        references = data.get("children")
        if (not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", data["id"])
                or not isinstance(references, list) or not 1 <= len(references) <= 12
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                       or not re.fullmatch(r"[a-f0-9]{32}", row["id"]) for row in references)
                or len({data["id"], *(row["id"] for row in references)}) != len(references) + 1):
            raise ValueError("Invalid matrix identity or child references.")
    if data.get("kind") == "protocol-job":
        references = data.get("children")
        if (not isinstance(data.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", data["id"])
                or not isinstance(references, list) or len(references) != 2
                or any(not isinstance(row, dict) or row.get("protocol") != protocol
                       or not isinstance(row.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", row["id"])
                       for row, protocol in zip(references, ["tcp", "udp"]))
                or len({data["id"], *(row["id"] for row in references)}) != 3):
            raise ValueError("Invalid Both job identity or child references.")
    if (data.get("settings") or {}).get("protocol") == "both" and data.get("kind") != "protocol-job":
        raise ValueError("Both settings require a protocol-job with separate TCP and UDP evidence.")
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    source_id = data.get("id")
    is_check = data.get("kind") == "diagnostic-check"
    if is_check and (type(data.get("schema_version")) is not int or data["schema_version"] != 1
                     or not isinstance(source_id, str) or not re.fullmatch(r"[a-f0-9]{32}", source_id)):
        raise ValueError("A diagnostic check requires schema 1 and a valid source identity.")
    if "lineage" in data:
        from .reruns import validate_lineage
        validate_lineage(data["lineage"])
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-f0-9]{32}", source_id):
            raise ValueError("Run lineage requires a valid source run identity.")
    if "settings" in data:
        if data.get("schema_version") != 1:
            raise ValueError("Unsupported run schema version.")
        record = copy.deepcopy(data)
        if is_check:
            from .checks import validate_check_settings, validate_check_result, validate_check_record
            validate_check_settings(data.get("check_type"), data["settings"])
            validate_check_result(data.get("check_type"), data.get("result"), data["settings"])
            if data.get("status") in TERMINAL:
                validate_check_record(data)
            elif ((data.get("result", {}).get("status") == "pending" and data.get("status") not in {"pending", "starting"})
                  or (data.get("result", {}).get("status") == "running" and data.get("status") != "running")
                  or data.get("result", {}).get("status") not in {"pending", "running"}):
                raise ValueError("An active check snapshot must retain an active check result.")
            record["settings"] = copy.deepcopy(data["settings"])
        else:
            record["settings"] = settings_from(data["settings"])
        if data.get("kind") == "flent-run":
            if data["settings"].get("flent_workload") != "rrul":
                raise ValueError("Flent runs must retain their native RRUL workload settings.")
            record["settings"]["flent_workload"] = "rrul"
        for key in ("metrics", "effective_settings", "series", "peer", "endpoint_view", "raw_stdout", "raw_stderr", "events", "request_id", "execution_request"):
            record.pop(key, None)
        record["created_at"] = timestamp(data.get("created_at"))
        record["finished_at"] = timestamp(data.get("finished_at"), optional=True)
        if data.get("status") not in TERMINAL | {"starting", "running", "queued"} | ({"pending"} if is_check else set()):
            raise ValueError("Invalid saved run status.")
        if data["status"] not in TERMINAL:
            record.update(status="interrupted", finished_at=record["finished_at"] or record["created_at"],
                          error="Imported snapshot of an unfinished session.")
            if is_check:
                record["result"].update(status="interrupted", finished_at=record["finished_at"],
                                        error="Imported snapshot of an unfinished check; no execution was resumed.")
                if record["result"]["time_quality"]["status"] == "pending":
                    from .time_quality import time_quality_result
                    record["result"]["time_quality"] = time_quality_result("unavailable", note="Imported before a clock observation was retained; no query was performed.")
                if "time_quality" in record:
                    record["time_quality"] = copy.deepcopy(record["result"]["time_quality"])
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
        from .endpoints import derive_endpoints
        record["endpoints"] = derive_endpoints(record)
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
    if is_check:
        if record["summary"] or record["intervals"] or record.get("test_parameters") is not None or record.get("native_result") is not None or record.get("iperf_version") is not None:
            raise ValueError("Diagnostic checks must not contain iPerf throughput evidence.")
        validate_check_record(record)
    else:
        validate_measurement_structure(record)
    if "annotations" in record:
        record["annotations"] = annotations_from(record["annotations"])
    if "diagnostics" in record:
        from .diagnostics import validate_diagnostics
        validate_diagnostics(record["diagnostics"])
    if "preflight" in record:
        from .preflight import validate_preflight
        validate_preflight(record["preflight"])
    if record.get("kind") == "flent-run":
        from .flent import validate_flent
        validate_flent(record.get("flent"))
        if record.get("status") == "completed" and record["flent"].get("status") != "captured":
            raise ValueError("A completed Flent run requires complete captured RRUL measurement evidence.")
        if record["settings"].get("flent_workload") != "rrul":
            raise ValueError("Flent runs must retain their native RRUL workload settings.")
    route = record.get("route")
    if route is not None:
        from .route import validate_route
        validate_route(route)
    from .endpoints import validate_endpoints, validate_context
    if "endpoints" in record:
        validate_endpoints(record["endpoints"], record)
    if "initiator_context" in record:
        validate_context(record["initiator_context"])
    record.update(id=uuid4().hex, event_count=len(events),
                  imported={"at": now(), "filename": filename[:255], "source_id": source_id,
                            "sha256": digest, "format": "app" if "settings" in data else "native-iperf"})
    return record, events


def prepare_job(data, filename):
    sources = data.get("runs")
    if not isinstance(sources, list) or len(sources) != 2:
        raise ValueError("A Both export must include exactly two protocol runs: TCP then UDP.")
    parent, events = prepare_run(data, filename, allow_job=True)
    if parent["settings"]["protocol"] != "both" or parent["summary"] or parent["intervals"] or events:
        raise ValueError("A Both job must keep measurements and logs inside its protocol runs.")
    children = [prepare_run(row, filename) for row in sources]
    references = data.get("children")
    if not isinstance(references, list) or len(references) != 2:
        raise ValueError("Invalid Both child references.")
    source_ids = set()
    for index, ((child, _), source, reference, protocol) in enumerate(zip(children, sources, references, ["tcp", "udp"]), 1):
        source_id = source.get("id")
        group = source.get("protocol_job")
        if (not isinstance(source_id, str) or not re.fullmatch(r"[a-f0-9]{32}", source_id)
                or source_id in source_ids or not isinstance(group, dict) or group.get("id") != data.get("id")
                or group.get("order") != index or group.get("protocol") != protocol
                or child["settings"]["protocol"] != protocol or reference != {"id": source_id, "protocol": protocol}):
            raise ValueError("Invalid or mismatched Both protocol identity.")
        source_ids.add(source_id)
        child["protocol_job"] = {"id": parent["id"], "source_id": data["id"], "protocol": protocol, "order": index}
    statuses = [row["status"] for row, _ in children]
    if parent["status"] == "completed" and statuses != ["completed", "completed"]:
        raise ValueError("A completed Both job requires two completed protocol runs.")
    if parent["status"] == "partial" and statuses.count("completed") != 1:
        raise ValueError("A partial Both job requires exactly one completed protocol run.")
    parent.pop("runs", None)
    parent["children"] = [{"id": child["id"], "protocol": child["settings"]["protocol"]} for child, _ in children]
    return [(parent, []), *children]


def prepare_matrix(data, filename):
    sources, references = data.get("runs"), data.get("children")
    if not isinstance(sources, list) or not isinstance(references, list) or not 1 <= len(sources) <= 12 or len(sources) != len(references):
        raise ValueError("A matrix export must contain every referenced child test (at most 12).")
    parent, events = prepare_run(data, filename, allow_job=True)
    if parent["summary"] or parent["intervals"] or events:
        raise ValueError("Matrix measurements and logs must remain in their individual tests.")
    children = [prepare_run(row, filename) for row in sources]
    source_ids, combinations = {data["id"]}, set()
    new_references = []
    for index, ((child, _), source, reference) in enumerate(zip(children, sources, references), 1):
        source_id, group, settings = source.get("id"), source.get("matrix_job"), child["settings"]
        expected = {"id": source_id, "protocol": settings["protocol"], "mode": settings["mode"],
                    "streams": settings["streams"], "order": index}
        combination = (settings["protocol"], settings["mode"], settings["streams"])
        if (not isinstance(source_id, str) or not re.fullmatch(r"[a-f0-9]{32}", source_id)
                or source_id in source_ids or settings["role"] != "client" or combination in combinations
                or not isinstance(group, dict) or group.get("id") != data["id"] or group.get("order") != index
                or reference != expected):
            raise ValueError("Invalid or mismatched matrix child identity/settings/order.")
        source_ids.add(source_id)
        combinations.add(combination)
        child["matrix_job"] = {"id": parent["id"], "source_id": data["id"], "order": index}
        new_references.append(dict(expected, id=child["id"]))
    statuses = [row["status"] for row, _ in children]
    if parent["status"] == "completed" and any(status != "completed" for status in statuses):
        raise ValueError("A completed matrix must contain only completed tests.")
    if parent["status"] == "partial" and not 0 < statuses.count("completed") < len(statuses):
        raise ValueError("A partial matrix must contain some completed tests and some incomplete tests.")
    parent.pop("runs", None)
    parent["children"] = new_references
    return [(parent, []), *children]


def prepare_source_documents(sources, filename):
    """Validate all sources before writes, remapping coherent job child identities."""
    if isinstance(sources, dict):
        sources = [sources]
    if not isinstance(sources, list) or not sources:
        raise ValueError("Choose one or more test documents within the import size budget.")
    prepared = []
    prepared_bytes = 0
    for source in sources:
        if isinstance(source, dict) and source.get("kind") == "protocol-job":
            rows = prepare_job(source, filename)
        elif isinstance(source, dict) and source.get("kind") == "matrix-job":
            rows = prepare_matrix(source, filename)
        else:
            rows = [prepare_run(source, filename)]
        prepared_bytes += len(json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if prepared_bytes > MAX_PREPARED_BYTES:
            raise ValueError("Prepared tests exceed the 32 MiB archive import budget. Import smaller groups separately.")
        prepared.extend(rows)
    return prepared


def finalize_import_lineage(prepared, existing_by_digest=None):
    """Resolve ancestry only against this complete, validated import batch.

    Existing digest matches can supply the local identity of a duplicate source.
    Arbitrary local IDs are deliberately never used to resolve imported links.
    This helper performs no storage writes or execution-request registration.
    """
    from .reruns import remap_lineage, validate_lineage, validate_lineage_graph
    existing_by_digest = existing_by_digest or {}
    source_digests, original, id_map = {}, {}, {}
    final_ids = dict(existing_by_digest)
    for record, _ in prepared:
        provenance = record["imported"]
        source_id, digest = provenance.get("source_id"), provenance["sha256"]
        final_id = final_ids.setdefault(digest, record["id"])
        if source_id is None:
            continue
        if not isinstance(source_id, str):
            if "lineage" in record:
                raise ValueError("Run lineage requires a valid source run identity.")
            continue
        if source_id in source_digests and source_digests[source_id] != digest:
            raise ValueError("An import contains conflicting documents for the same source run identity.")
        source_digests[source_id] = digest
        id_map[source_id] = final_id
        row = {"id": source_id}
        if "lineage" in record:
            lineage = validate_lineage(record["lineage"])
            # An external marker is provenance, not a way to evade cycle checks
            # when its source is included in this same batch.
            row["lineage"] = dict(lineage, source_run_id=lineage["source_run_id"] or lineage["external_source_id"],
                                  external_source_id=None)
        original[source_id] = row
    validate_lineage_graph(list(original.values()))
    replacements, final_graph = [], []
    for record, _ in prepared:
        row = {"id": final_ids[record["imported"]["sha256"]]}
        if "lineage" in record:
            lineage = remap_lineage(record["lineage"], id_map)
            replacements.append((record, lineage))
            row["lineage"] = lineage
        final_graph.append(row)
    validate_lineage_graph(final_graph)
    for record, lineage in replacements:
        record["lineage"] = lineage
    return id_map


def import_runs(manager, data, filename):
    sources = [data]
    if isinstance(data, dict) and data.get("kind") in {"multi-comparison", "diagnostic-comparison"}:
        sources = data.get("runs")
        if not isinstance(sources, list) or len(sources) < 2:
            raise ValueError("A comparison export must contain at least two individual runs.")
    elif isinstance(data, dict) and "before" in data and "after" in data:
        sources = [data["before"], data["after"]]
    if len(sources) > 1 and any(isinstance(source, dict) and source.get("kind") in {"protocol-job", "matrix-job"} for source in sources):
        raise ValueError("Comparison imports must contain individual tests; import complete jobs or projects separately.")
    try:
        prepared = prepare_source_documents(sources, filename)
    except (TypeError, AttributeError, OverflowError, RecursionError):
        raise ValueError("Invalid saved test structure.") from None
    imported, duplicates = [], []
    with manager.lock:
        if manager.storage_error:
            raise RuntimeError("Restore archive storage access before importing tests.")
        existing = {}
        for record in manager.storage.records():
            digest = (record.get("imported") or {}).get("sha256")
            if digest:
                existing[digest] = record["id"]
        is_job = isinstance(data, dict) and data.get("kind") in {"protocol-job", "matrix-job"}
        if is_job and prepared[0][0]["imported"]["sha256"] in existing:
            parent_id = existing[prepared[0][0]["imported"]["sha256"]]
            parent = manager.read(parent_id)
            return {"imported": [], "duplicates": [parent_id, *(row["id"] for row in parent["children"])]}
        finalize_import_lineage(prepared, None if is_job else existing)
        with manager.storage.transaction() as connection:
            for record, events in prepared:
                digest = record["imported"]["sha256"]
                if digest in existing and not is_job:
                    duplicates.append(existing[digest])
                    continue
                # A standalone file cannot recreate its project's catalog/baseline
                # or a parent job. Retain source identities as provenance instead
                # of attaching evidence to coincidentally matching local IDs.
                source_project = record["settings"].get("project_id")
                if source_project:
                    record["imported"]["source_project_id"] = source_project
                    record["settings"]["project_id"] = None
                if not is_job:
                    for group in ("protocol_job", "matrix_job"):
                        if group in record:
                            record["imported"]["source_group"] = record.pop(group)
                manager.storage.save(record, connection)
                manager.storage.insert_events(record["id"], events, connection)
                existing[digest] = record["id"]
                imported.append(record["id"])
    return {"imported": imported, "duplicates": duplicates}
