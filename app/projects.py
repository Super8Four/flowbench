"""Named change groups and measured before/after summaries in archive metadata."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from .annotations import identifier
from .metrics import comparison, measurements


def now():
    return datetime.now(timezone.utc).isoformat()


def project_fields(data, creating=False):
    if not isinstance(data, dict):
        raise ValueError("Send project details as JSON.")
    result = {}
    for key, limit in (("name", 200), ("description", 4000)):
        if key in data or creating:
            text = data.get(key, "")
            if not isinstance(text, str) or len(text) > limit or (key == "name" and not text.strip()):
                raise ValueError(f"Project {key} must be text up to {limit} characters" + (" and cannot be empty." if key == "name" else "."))
            result[key] = text.strip()
    if "baseline_id" in data or creating:
        result["baseline_id"] = identifier(data.get("baseline_id"), "Baseline ID", nullable=True)
    return result


def is_leaf(record):
    return not record.get("children") and record.get("kind") not in {"protocol-job", "matrix-job"}


def has_baseline_evidence(record):
    if record.get("kind") == "diagnostic-check":
        from .metrics import check_comparison_eligible
        return (record.get("status") == "completed" and check_comparison_eligible(record)
                and bool((record.get("result") or {}).get("attempts")))
    return bool(measurements(record))


def catalog_from(data, records):
    """Validate rollback metadata against the same run snapshot before importing."""
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("projects"), list):
        raise ValueError("Invalid saved project catalog.")
    result, ids = {"schema_version": 1, "projects": []}, set()
    by_id = {row["id"]: row for row in records}
    for source in data["projects"]:
        if not isinstance(source, dict):
            raise ValueError("Invalid saved project.")
        project_id = identifier(source.get("id"), "Project ID")
        if project_id in ids:
            raise ValueError("Saved project IDs must be unique.")
        ids.add(project_id)
        project_fields(source, creating=True)
        for key in ("created_at", "updated_at"):
            try:
                date = datetime.fromisoformat(source[key].replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("Saved project dates must include a timezone.") from None
        baseline_id = source.get("baseline_id")
        if baseline_id:
            baseline = by_id.get(baseline_id)
            if (not baseline or baseline["settings"].get("project_id") != project_id or not is_leaf(baseline)
                    or baseline["status"] != "completed" or not has_baseline_evidence(baseline)):
                raise ValueError("Saved project baseline is missing or not a completed measured member.")
        result["projects"].append(copy.deepcopy(source))
    return result


class ProjectStore:
    def __init__(self, manager):
        self.manager = manager

    def catalog(self, connection=None):
        return self.manager.storage.metadata("projects", connection) or {"schema_version": 1, "projects": []}

    def list(self):
        with self.manager.lock:
            catalog = self.catalog()
            counts = {}
            for record in self.manager.storage.records():
                project_id = record.get("settings", {}).get("project_id")
                counts[project_id] = counts.get(project_id, 0) + (1 if is_leaf(record) else 0)
            return [dict(copy.deepcopy(row), run_count=counts.get(row["id"], 0))
                    for row in sorted(catalog["projects"], key=lambda row: row["created_at"], reverse=True)]

    def read(self, project_id):
        identifier(project_id, "Project ID")
        for row in self.catalog()["projects"]:
            if row["id"] == project_id:
                return copy.deepcopy(row)
        raise KeyError("Project not found.")

    def validate_baseline(self, project):
        baseline_id = project.get("baseline_id")
        if baseline_id is None:
            return
        try:
            baseline = self.manager.read(baseline_id)
        except KeyError:
            raise ValueError("Choose a completed measured run in this project as baseline.") from None
        if (baseline.get("settings", {}).get("project_id") != project["id"] or not is_leaf(baseline)
                or baseline.get("status") != "completed" or not has_baseline_evidence(baseline)):
            raise ValueError("Choose a completed measured run in this project as baseline.")

    def create(self, data):
        fields = project_fields(data, creating=True)
        if fields["baseline_id"]:
            raise ValueError("Create the project, assign runs, then choose its baseline.")
        project = dict(fields, id=uuid4().hex, created_at=now(), updated_at=now())
        with self.manager.lock:
            if self.manager.storage_error:
                raise RuntimeError("Restore archive storage access before editing projects.")
            with self.manager.storage.transaction() as connection:
                catalog = self.catalog(connection)
                catalog["projects"].append(project)
                self.manager.storage.save_metadata("projects", catalog, connection)
        return project

    def update(self, project_id, data):
        fields = project_fields(data)
        with self.manager.lock:
            if self.manager.storage_error:
                raise RuntimeError("Restore archive storage access before editing projects.")
            project = dict(self.read(project_id), **fields, updated_at=now())
            self.validate_baseline(project)
            with self.manager.storage.transaction() as connection:
                catalog = self.catalog(connection)
                catalog["projects"] = [project if row["id"] == project_id else row for row in catalog["projects"]]
                self.manager.storage.save_metadata("projects", catalog, connection)
        return project

    def report(self, project_id):
        with self.manager.lock:
            project = self.read(project_id)
            runs = [self.manager.read(row["id"]) for row in self.manager.storage.records()
                    if row.get("settings", {}).get("project_id") == project_id and is_leaf(row)]
            baseline = next((row for row in runs if row["id"] == project.get("baseline_id")), None)
            rows, excluded = [], []
            for record in sorted(runs, key=lambda row: row["created_at"]):
                if baseline and record["id"] == baseline["id"]:
                    continue
                if record["status"] != "completed":
                    excluded.append({"id": record["id"], "reason": f"Run status is {record['status']}."})
                    continue
                if not baseline:
                    continue
                try:
                    if baseline.get("kind") == "diagnostic-check" or record.get("kind") == "diagnostic-check":
                        from .metrics import check_comparison
                        report = check_comparison(baseline, record)
                        report["result"] = "observations compared"
                        report["caveat"] = " ".join(report["caveats"])
                        rows.append(report)
                        continue
                    report = comparison(baseline, record)
                except ValueError as exc:
                    excluded.append({"id": record["id"], "reason": str(exc)})
                    continue
                directions = {row["direction"] for row in report["metrics"] if row["direction"] not in {"unchanged", "volume change"}}
                report["result"] = ("mixed" if len(directions) > 1 else next(iter(directions),
                                     "volume change" if any(row["direction"] == "volume change" for row in report["metrics"]) else "unchanged"))
                report["comparable"] = not report["settings_differences"]
                report["caveat"] = ("Settings differ; this measured change does not establish an improvement or regression."
                                    if not report["comparable"] else report["interpretation"])
                rows.append(report)
            # Return evidence for baseline selection, never invent a project average.
            return {"schema_version": 1, "project": project,
                    "runs": [{"id": row["id"], "created_at": row["created_at"], "status": row["status"],
                              "settings": row["settings"], "metrics": measurements(row),
                              "kind": row.get("kind"), "check_type": row.get("check_type"), "result": row.get("result")} for row in runs],
                    "comparisons": rows, "excluded": excluded,
                    "note": "Choose a completed measured baseline. Differences describe observed tests; matching settings, repeated runs and environmental context are needed to verify a change."}

    def export(self, project_id):
        with self.manager.lock:
            project = self.read(project_id)
            records = [self.manager.read(row["id"]) for row in self.manager.storage.records()
                       if row.get("settings", {}).get("project_id") == project_id]
            ids = {row["id"] for row in records}
            by_id = {row["id"]: row for row in records}
            for record in records:
                for reference in record.get("children", []):
                    child = self.manager.read(reference["id"])
                    if child["settings"].get("project_id") != project_id:
                        raise ValueError("This job has children in another project. Restore its complete project membership before exporting.")
                for key in ("protocol_job", "matrix_job"):
                    group = record.get(key)
                    if group is not None and not isinstance(group, dict):
                        raise ValueError("Invalid saved job membership; the project was not exported.")
                    parent = by_id.get((group or {}).get("id"))
                    if parent and not any(row["id"] == record["id"] for row in parent.get("children", [])):
                        raise ValueError("This run has inconsistent job membership; the project was not exported.")
            # Complete parent exports include their children once, preserving logs.
            roots = [row for row in records if not any(
                (row.get(key) or {}).get("id") in ids for key in ("protocol_job", "matrix_job"))]
            return {"schema_version": 1, "kind": "project-export", "project": project,
                    "runs": [self.manager.export(row["id"]) for row in roots]}

    def import_bundle(self, data, filename, prepare_source):
        """Prepare every run, remap IDs and commit the complete project once.

        prepare_source(source, filename) returns [(record, events), ...] using the
        existing run/protocol/matrix import validators. It must not write storage.
        """
        if not isinstance(data, dict) or data.get("kind") != "project-export" or data.get("schema_version") != 1:
            raise ValueError("Upload a schema 1 project export.")
        source_project = data.get("project")
        fields = project_fields(source_project, creating=True)
        source_id = identifier(source_project.get("id"), "Project ID")
        for key in ("created_at", "updated_at"):
            try:
                date = datetime.fromisoformat(source_project[key].replace("Z", "+00:00"))
                if date.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ValueError("Exported project dates must include a timezone.") from None
        sources = data.get("runs")
        if not isinstance(sources, list):
            raise ValueError("A project import must contain an array of runs within the import size budget.")
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        project_id, prepared, remap, prepared_bytes = uuid4().hex, [], {}, 0
        for source in sources:
            rows = prepare_source(source, filename)
            prepared_bytes += len(json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if prepared_bytes > 32 * 1024 * 1024:
                raise ValueError("Prepared project exceeds the 32 MiB archive import budget. Export smaller groups separately.")
            for record, events in rows:
                old_id = identifier(record["imported"].get("source_id"), "Source run ID")
                if old_id in remap or record["settings"].get("project_id") != source_id:
                    raise ValueError("Project runs must have unique IDs and belong to the exported project.")
                remap[old_id] = record["id"]
                record["settings"]["project_id"] = project_id
                prepared.append((record, events))
        from .imports import finalize_import_lineage
        finalize_import_lineage(prepared)
        baseline_id = fields["baseline_id"]
        if baseline_id is not None and baseline_id not in remap:
            raise ValueError("The exported baseline is missing from this project.")
        fields["baseline_id"] = remap.get(baseline_id)
        if fields["baseline_id"]:
            baseline = next(row for row, _ in prepared if row["id"] == fields["baseline_id"])
            if not is_leaf(baseline) or baseline["status"] != "completed" or not has_baseline_evidence(baseline):
                raise ValueError("The exported baseline must be a completed measured leaf run.")
        project = dict(fields, id=project_id, created_at=source_project["created_at"], updated_at=source_project["updated_at"],
                       imported={"source_id": source_id, "sha256": digest, "filename": filename[:255], "at": now()})
        with self.manager.lock:
            if self.manager.storage_error:
                raise RuntimeError("Restore archive storage access before importing projects.")
            with self.manager.storage.transaction() as connection:
                catalog = self.catalog(connection)
                duplicate = next((row for row in catalog["projects"] if (row.get("imported") or {}).get("sha256") == digest), None)
                if duplicate:
                    return {"project": duplicate, "imported": [], "duplicates": [duplicate["id"]]}
                for record, events in prepared:
                    self.manager.storage.save(record, connection)
                    self.manager.storage.insert_events(record["id"], events, connection)
                catalog["projects"].append(project)
                self.manager.storage.save_metadata("projects", catalog, connection)
        return {"project": project, "imported": [row["id"] for row, _ in prepared], "duplicates": []}


def validate_project_assignment(manager, settings):
    project_id = settings.get("project_id")
    if project_id:
        try:
            ProjectStore(manager).read(project_id)
        except KeyError:
            raise ValueError("Choose an existing project or leave the project empty.") from None


def protect_baseline_membership(manager, record):
    for project in ProjectStore(manager).catalog()["projects"]:
        if project.get("baseline_id") == record["id"] and record["settings"].get("project_id") != project["id"]:
            raise ValueError("Clear or change the project baseline before moving this run.")
