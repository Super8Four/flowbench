import copy
import tempfile
import unittest
from unittest.mock import patch

from app.annotations import annotations_from, annotate_run, settings_metadata, tags_from
from app.projects import ProjectStore
from app.runner import RunManager, settings_from


def sample_record(run_id="a" * 32, **changes):
    record = {"schema_version": 1, "id": run_id, "created_at": "2026-01-01T10:00:00+00:00",
              "finished_at": "2026-01-01T10:00:10+00:00", "status": "completed",
              "settings": settings_from({"target": "127.0.0.1"}), "iperf_version": "iperf 3.22",
              "output_format": "json", "exit_code": 0, "command": [], "error": None,
              "summary": {"sum_received": {"bits_per_second": 100e6}}, "intervals": [],
              "test_parameters": None, "event_count": 0}
    record.update(changes)
    return record


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.record = sample_record()
        self.manager.save(self.record)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_metadata_optional_and_bounded(self):
        self.assertEqual(settings_metadata({}), {})
        self.assertEqual(tags_from({"site": " Lab "}), {"site": "Lab"})
        self.assertEqual(settings_metadata({"project_id": ""}), {"project_id": None})
        for data in ({"tags": {"unknown": "x"}}, {"tags": {"site": "x" * 101}},
                     {"tags": []}, {"project_id": "../../../private"}):
            with self.assertRaises(ValueError):
                settings_metadata(data)

    def test_timestamped_notes_edit_restart_and_export(self):
        updated = annotate_run(self.manager, self.record["id"],
                               {"tags": {"ticket": "TEST-1"}, "annotations": [{"seconds": 1.5, "text": "Link changed"}],
                                "notes": "Summary"})
        note = updated["annotations"][0]
        self.assertEqual(len(note["id"]), 32)
        self.assertIn("+00:00", note["timestamp"])
        edited = dict(note, text="Link recovered")
        annotate_run(self.manager, self.record["id"], {"annotations": [edited]})
        self.assertEqual(self.manager.export(self.record["id"])["annotations"], [edited])
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, "missing-iperf")
        self.assertEqual(self.manager.read(self.record["id"])["annotations"], [edited])
        self.assertEqual(self.manager.read(self.record["id"])["settings"]["tags"], {"ticket": "TEST-1"})
        annotate_run(self.manager, self.record["id"], {"annotations": []})
        self.assertEqual(self.manager.read(self.record["id"])["annotations"], [])

    def test_invalid_timeline_and_partial_patch_never_commit(self):
        valid = {"id": "b" * 32, "timestamp": "2026-01-01T10:00:00Z", "text": "note"}
        for value in ([dict(valid, seconds=float("nan"))], [dict(valid, seconds=True)],
                      [dict(valid, seconds=-1)], [dict(valid, timestamp="yesterday")],
                      [dict(valid, timestamp="2026-01-01")], [dict(valid, text=" ")],
                      [valid, valid], [dict(valid, unexpected="x")], [valid] * 201):
            with self.assertRaises(ValueError):
                annotations_from(value)
        with self.assertRaises(ValueError):
            annotate_run(self.manager, self.record["id"], {"notes": "must not save", "tags": {"site": False}})
        self.assertEqual(self.manager.read(self.record["id"]), self.record)

    def test_commit_failure_does_not_modify_live_record(self):
        self.manager.record = copy.deepcopy(self.record)
        with patch.object(self.manager.storage, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                annotate_run(self.manager, self.record["id"], {"notes": "unsaved"})
        self.assertEqual(self.manager.record, self.record)
        self.assertEqual(self.manager.storage.read(self.record["id"]), self.record)

    def test_group_membership_atomic_and_baseline_protected(self):
        projects = ProjectStore(self.manager)
        left = projects.create({"name": "Before"}); right = projects.create({"name": "After"})
        parent = sample_record("b" * 32, kind="matrix-job", summary={},
                               children=[{"id": self.record["id"]}])
        parent["settings"]["project_id"] = left["id"]
        self.record["settings"]["project_id"] = left["id"]
        self.record["matrix_job"] = {"id": parent["id"]}
        self.manager.save(parent); self.manager.save(self.record)
        with self.assertRaises(ValueError):
            annotate_run(self.manager, self.record["id"], {"project_id": right["id"]})
        projects.update(left["id"], {"baseline_id": self.record["id"]})
        with self.assertRaises(ValueError):
            annotate_run(self.manager, parent["id"], {"project_id": right["id"]})
        self.assertEqual(self.manager.read(parent["id"])["settings"]["project_id"], left["id"])
        projects.update(left["id"], {"baseline_id": None})
        annotate_run(self.manager, parent["id"], {"project_id": right["id"], "tags": {"site": "Lab"}})
        self.assertEqual(self.manager.read(self.record["id"])["settings"]["project_id"], right["id"])
        self.assertEqual(self.manager.read(self.record["id"])["settings"]["tags"], {"site": "Lab"})


if __name__ == "__main__":
    unittest.main()
