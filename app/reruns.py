"""Immutable execution lineage and local request identity (never imported tokens)."""

import copy
import hashlib
import json
import re


class AdmissionConflict(RuntimeError):
    pass


class AdmissionUnavailable(RuntimeError):
    def __init__(self, message, record=None):
        super().__init__(message)
        self.record = record


def fingerprint(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Execution intent must be finite JSON.") from None
    if len(encoded.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Execution intent exceeds the 1 MiB configuration budget.")
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def request_identity(data, action):
    if not isinstance(data, dict):
        raise ValueError("Send execution settings as a JSON object.")
    token = data.get("request_id")
    if token is not None and (not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token)):
        raise ValueError("request_id must be a lowercase UUID without hyphens.")
    return token, fingerprint({"action": action, "request": {k: v for k, v in data.items() if k != "request_id"}})


def validate_lineage(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "action", "source_run_id", "external_source_id"}:
        raise ValueError("Invalid run lineage.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["action"] not in ("run_again", "configured_from"):
        raise ValueError("Invalid run lineage version or action.")
    ids = [value["source_run_id"], value["external_source_id"]]
    if sum(item is not None for item in ids) != 1 or any(item is not None and (not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item)) for item in ids):
        raise ValueError("Run lineage requires exactly one valid source identifier.")
    return copy.deepcopy(value)


def lineage_for(source_id, action="run_again"):
    return validate_lineage({"schema_version": 1, "action": action, "source_run_id": source_id, "external_source_id": None})


def remap_lineage(value, id_map):
    result = validate_lineage(value)
    source = result["source_run_id"] or result["external_source_id"]
    result.update(source_run_id=id_map.get(source), external_source_id=None if source in id_map else source)
    return result


def validate_lineage_graph(records):
    by_id = {row["id"]: row for row in records}
    edges = {}
    for row in records:
        if "lineage" in row:
            lineage = validate_lineage(row["lineage"])
            parent = lineage["source_run_id"]
            if parent in by_id:
                edges[row["id"]] = parent
    done = set()
    for start in edges:
        path, node = set(), start
        while node in edges and node not in done:
            if node in path:
                raise ValueError("Run lineage contains a cycle.")
            path.add(node)
            node = edges[node]
        done.update(path)
