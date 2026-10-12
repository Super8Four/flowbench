"""Validated user metadata, separate from captured measurement evidence."""

import copy
from datetime import datetime, timezone
import math
import re
from uuid import uuid4

TAG_KEYS = {"site", "device", "circuit", "ticket"}
MAX_ANNOTATIONS = 200


def identifier(value, label="ID", nullable=False):
    if nullable and value in (None, ""):
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError(f"{label} must be a saved ID.")
    return value


def tags_from(value):
    if not isinstance(value, dict) or set(value) - TAG_KEYS:
        raise ValueError("Tags must contain only site, device, circuit and ticket.")
    tags = {}
    for key, text in value.items():
        if not isinstance(text, str) or len(text) > 100:
            raise ValueError(f"{key} tag must be text up to 100 characters.")
        tags[key] = text.strip()
    return tags


def settings_metadata(data):
    result = {}
    if "project_id" in data:
        result["project_id"] = identifier(data["project_id"], "Project ID", nullable=True)
    if "tags" in data:
        result["tags"] = tags_from(data["tags"])
    return result


def annotations_from(value, allow_defaults=False):
    if not isinstance(value, list) or len(value) > MAX_ANNOTATIONS:
        raise ValueError(f"Timeline notes must be an array of at most {MAX_ANNOTATIONS} notes.")
    result, ids = [], set()
    for source in value:
        if not isinstance(source, dict) or set(source) - {"id", "timestamp", "seconds", "text"}:
            raise ValueError("Invalid timeline note fields.")
        note = copy.deepcopy(source)
        if allow_defaults:
            note.setdefault("id", uuid4().hex)
            note.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        note["id"] = identifier(note.get("id"), "Note ID")
        if note["id"] in ids:
            raise ValueError("Timeline note IDs must be unique.")
        ids.add(note["id"])
        stamp = note.get("timestamp")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00")) if isinstance(stamp, str) else None
            if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError()
        except (ValueError, OverflowError):
            raise ValueError("Timeline note timestamps must be ISO dates with a timezone.") from None
        if not isinstance(note.get("text"), str) or not note["text"].strip() or len(note["text"]) > 2000:
            raise ValueError("Timeline note text must contain 1 to 2000 characters.")
        note["text"] = note["text"].strip()
        seconds = note.get("seconds")
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                                    or not math.isfinite(seconds) or not 0 <= seconds <= 86400):
            raise ValueError("Timeline elapsed seconds must be a finite number from 0 to 86400.")
        result.append(note)
    return result


def patch_record(record, data):
    if not isinstance(data, dict):
        raise ValueError("Send run labels, tags or timeline notes as JSON.")
    result = copy.deepcopy(record)
    for key, limit in (("label", 200), ("notes", 4000)):
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > limit:
                raise ValueError(f"{key} must be text up to {limit} characters.")
            result["settings"][key] = data[key].strip()
    result["settings"].update(settings_metadata(data))
    if "annotations" in data:
        result["annotations"] = annotations_from(data["annotations"], allow_defaults=True)
    return result


def annotate_run(manager, run_id, data):
    """Save edits once and update live checkpoints only after a successful commit."""
    from .projects import validate_project_assignment, protect_baseline_membership

    with manager.lock:
        if manager.storage_error:
            raise RuntimeError("Restore archive storage access before editing tests.")
        original = manager.read(run_id)
        record = patch_record(original, data)
        validate_project_assignment(manager, record["settings"])
        for key in ("protocol_job", "matrix_job"):
            group_id = (record.get(key) or {}).get("id")
            if group_id and "project_id" in data:
                try:
                    parent = manager.read(group_id)
                except KeyError:
                    parent = None  # Standalone imported leaf can retain source context.
                if parent and parent["settings"].get("project_id") != record["settings"].get("project_id"):
                    raise ValueError("Move the complete job to keep its measured children in one project.")
        updates = [record]
        if record.get("children"):
            for child in record["children"]:
                child_record = manager.read(child["id"])
                # Timeline notes describe this run, while grouping and endpoint tags
                # are shared by every measured child in a controlled job.
                shared = {key: data[key] for key in ("project_id", "tags") if key in data}
                updates.append(patch_record(child_record, shared))
        for updated in updates:
            protect_baseline_membership(manager, updated)
        with manager.storage.transaction() as connection:
            for updated in updates:
                manager.storage.save(updated, connection)
        for updated in updates:
            for attribute in ("record", "job"):
                live = getattr(manager, attribute, None)
                if live and live["id"] == updated["id"]:
                    setattr(manager, attribute, copy.deepcopy(updated))
            listener = getattr(manager, "listener", None)
            if listener and listener.record and listener.record["id"] == updated["id"]:
                listener.record = copy.deepcopy(updated)
        return record
