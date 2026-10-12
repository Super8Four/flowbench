import copy
from concurrent.futures import ThreadPoolExecutor
import json
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
try:
    from test_annotations import sample_record
except ModuleNotFoundError:
    from tests.test_annotations import sample_record


class ProjectImportAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": "missing-iperf"})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["iperf_runs"]
        self.project = self.client.post('/api/projects', json={"name": "Lab change", "description": "Fixture"}).get_json()
        self.before = sample_record()
        self.before["settings"].update(project_id=self.project["id"], tags={"site": "Lab", "ticket": "TEST-42"})
        self.before["annotations"] = [{"id": "f" * 32, "timestamp": "2026-01-01T10:00:02Z",
                                       "seconds": 2, "text": "Change applied <fixture>"}]
        self.manager.save(self.before)
        self.client.patch('/api/projects/' + self.project["id"], json={"baseline_id": self.before["id"]})

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def bundle(self):
        return self.client.get('/api/projects/' + self.project["id"] + '/export').get_json()

    def upload(self, bundle):
        return self.client.post('/api/projects/import', json={"filename": "fixture.json", "content": json.dumps(bundle)})

    def test_json_xml_roundtrip_preserves_tags_annotations_dates_and_baseline(self):
        for format_name in ('json', 'xml'):
            export = self.client.get(f'/api/projects/{self.project["id"]}/export?format={format_name}')
            self.assertEqual(export.status_code, 200)
            self.assertIn('no-store', export.headers['Cache-Control'])
            result = self.client.post('/api/projects/import', json={"filename": f'fixture.{format_name}',
                                                                   "content": export.data.decode()})
            self.assertEqual(result.status_code, 200, result.get_json())
            value = result.get_json()
            if format_name == 'json':
                imported_project = value['project']; imported_id = value['imported'][0]
                imported = self.manager.export(imported_id)
                self.assertEqual(imported['settings']['tags'], self.before['settings']['tags'])
                self.assertEqual(imported['annotations'], self.before['annotations'])
                self.assertEqual(imported_project['baseline_id'], imported_id)
                self.assertEqual(imported_project['created_at'], self.project['created_at'])
            else:
                self.assertEqual(value['imported'], [])
                self.assertEqual(value['duplicates'], [imported_project['id']])
        report = self.client.get('/api/projects/' + imported_project['id']).get_json()
        self.assertEqual(report['project']['baseline_id'], imported_id)
        self.assertEqual(report['runs'][0]['settings']['project_id'], imported_project['id'])

    def test_active_session_import_does_not_restart_cancel_or_mutate_it(self):
        active = sample_record('b' * 32, status='running', finished_at=None)
        self.manager.save(active)
        self.manager.record = copy.deepcopy(active)
        self.manager.active = active['id']
        result = self.upload(self.bundle())
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(self.manager.active, active['id'])
        self.assertEqual(self.manager.record, active)
        self.assertFalse(self.manager.cancel_requested)
        self.assertIsNone(self.manager.process)
        self.manager.active = None

    def test_import_validation_atomic_baseline_failure_empty_measurements_and_duplicate_ids(self):
        original = self.bundle()
        variants = []
        missing = copy.deepcopy(original); missing['project']['baseline_id'] = 'e' * 32; variants.append(missing)
        failed = copy.deepcopy(original); failed['runs'][0]['status'] = 'failed'; variants.append(failed)
        unfinished = copy.deepcopy(original); unfinished['runs'][0]['finished_at'] = None; variants.append(unfinished)
        empty = copy.deepcopy(original); empty['runs'][0]['summary'] = {}; variants.append(empty)
        duplicate = copy.deepcopy(original); duplicate['runs'].append(copy.deepcopy(duplicate['runs'][0])); variants.append(duplicate)
        foreign = copy.deepcopy(original); foreign['runs'][0]['settings']['project_id'] = 'e' * 32; variants.append(foreign)
        for bundle in variants:
            result = self.upload(bundle)
            self.assertEqual(result.status_code, 400, result.get_json())
            self.assertEqual(len(self.manager.storage.records()), 1)
            self.assertEqual(len(self.client.get('/api/projects').get_json()['projects']), 1)

    def test_concurrent_identical_project_import_commits_one_copy(self):
        content = json.dumps(self.bundle())
        def upload(_):
            with self.app.test_client() as client:
                response = client.post('/api/projects/import', json={'filename': 'fixture.json', 'content': content})
                return response.status_code, response.get_json()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(upload, range(4)))
        self.assertEqual([code for code, _ in results], [200] * 4)
        self.assertEqual(sum(bool(value['imported']) for _, value in results), 1)
        self.assertEqual(len(self.manager.storage.records()), 2)
        self.assertEqual(len(self.client.get('/api/projects').get_json()['projects']), 2)

    def test_api_storage_failure_rolls_back_every_run_and_metadata(self):
        bundle = self.bundle()
        with patch.object(self.manager.storage, 'save_metadata', side_effect=OSError('fixture disk full')):
            result = self.upload(bundle)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(len(self.manager.storage.records()), 1)
        self.assertEqual(len(self.client.get('/api/projects').get_json()['projects']), 1)

    def test_standalone_import_keeps_project_and_group_as_provenance(self):
        source = self.manager.export(self.before['id'])
        source['matrix_job'] = {'id': 'b' * 32, 'order': 1}
        result = self.client.post('/api/runs/import', json={'filename': 'run.json', 'content': json.dumps(source)})
        self.assertEqual(result.status_code, 200, result.get_json())
        imported = self.manager.read(result.get_json()['imported'][0])
        self.assertIsNone(imported['settings']['project_id'])
        self.assertEqual(imported['imported']['source_project_id'], self.project['id'])
        self.assertEqual(imported['imported']['source_group'], source['matrix_job'])
        self.assertNotIn('matrix_job', imported)
        self.assertEqual(imported['annotations'], source['annotations'])

    def test_empty_project_import_and_clear_baseline_roundtrip(self):
        empty = self.client.post('/api/projects', json={'name': 'Empty'}).get_json()
        bundle = self.client.get('/api/projects/' + empty['id'] + '/export').get_json()
        result = self.upload(bundle)
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['imported'], [])
        self.assertIsNone(result.get_json()['project']['baseline_id'])
        cleared = self.client.patch('/api/projects/' + self.project['id'], json={'baseline_id': None})
        self.assertEqual(cleared.status_code, 200)
        moved = self.client.patch('/api/runs/' + self.before['id'], json={'project_id': empty['id']})
        self.assertEqual(moved.status_code, 200)

    def test_comparison_import_rejects_nested_jobs_before_any_write(self):
        job = copy.deepcopy(self.manager.export(self.before['id']))
        job.update(id='b' * 32, kind='matrix-job', summary={}, children=[], runs=[])
        sources = {'schema_version': 1, 'kind': 'multi-comparison',
                   'runs': [self.manager.export(self.before['id']), job]}
        result = self.client.post('/api/runs/import', json={'filename': 'comparison.json', 'content': json.dumps(sources)})
        self.assertEqual(result.status_code, 400, result.get_json())
        self.assertIn('job', result.get_json()['error'].lower())
        self.assertEqual(len(self.manager.storage.records()), 1)


if __name__ == '__main__':
    unittest.main()
