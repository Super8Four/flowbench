"""Cross-feature archive contracts, using synthetic evidence and no traffic."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from app.diagnostics import DiagnosticsCapture
from app.exports import xml_export
from app.runner import settings_from
from test_imports import saved_run


class WorkflowPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.open_app()

    def open_app(self):
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name,
                               'IPERF_PATH': 'missing-iperf'})
        self.manager = self.app.extensions['iperf_runs']
        self.client = self.app.test_client()

    def restart(self):
        self.manager.shutdown()
        self.open_app()

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def checked(self, response, status=200):
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def project(self):
        return self.checked(self.client.post('/api/projects', json={
            'name': 'Synthetic loopback change', 'description': 'No external peers'}), 201)

    def save_evidence(self, run_id='a' * 32, project_id=None, rate=100e6):
        record = saved_run()
        record['id'] = run_id
        record['settings'] = settings_from({'target': '127.0.0.1', 'duration': 10,
            'project_id': project_id, 'tags': {'site': 'Lab', 'device': 'Fixture',
            'circuit': 'Loopback', 'ticket': 'TEST-1'}, 'latency': True,
            'endpoint_health': True, 'mtu': True, 'traceroute': True})
        record['summary']['sum_received']['bits_per_second'] = rate
        # Capability absence is real, while throughput above is explicitly a fixture.
        with patch('app.diagnostics.shutil.which', return_value=None), patch.object(
                DiagnosticsCapture, '_health', return_value={
                    'status': 'unavailable', 'source': 'synthetic fixture',
                    'cpu': None, 'interfaces': []}):
            capture = DiagnosticsCapture(record['settings'])
            capture.baseline()
            record['diagnostics'] = capture.finish()
        record['route'] = {'status': 'unavailable', 'hops': [], 'raw_output': '',
                           'note': 'Synthetic fixture: native tool unavailable.'}
        events = record.pop('events')
        record['event_count'] = len(events)
        with self.manager.storage.transaction() as connection:
            self.manager.storage.save(record, connection)
            self.manager.storage.insert_events(run_id, events, connection)
        return self.manager.export(run_id)

    def upload(self, path, document, xml=False):
        return self.client.post(path, json={'filename': 'fixture.xml' if xml else 'fixture.json',
            'content': xml_export(document).decode() if xml else json.dumps(document)})

    def test_native_diagnostic_evidence_is_in_sqlite_and_survives_reopen(self):
        before = self.save_evidence()
        self.assertEqual(before['diagnostics']['latency']['metric'], 'ICMP echo RTT')
        self.assertEqual(before['diagnostics']['latency']['baseline']['status'], 'unavailable')
        self.assertIsNone(before['diagnostics']['latency']['mean_increase_ms'])
        self.assertEqual(before['diagnostics']['endpoint_health']['remote']['status'], 'unavailable')
        with closing(sqlite3.connect(self.manager.storage.path)) as database:
            actual = json.loads(database.execute('SELECT document FROM runs WHERE id=?', (before['id'],)).fetchone()[0])
            self.assertEqual(actual['diagnostics'], before['diagnostics'])
            self.assertEqual(actual['route'], before['route'])
            self.assertEqual(database.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.restart()
        self.assertEqual(self.manager.export(before['id']), before)

    def test_diagnostic_tags_and_timeline_roundtrip_json_xml_without_fabrication(self):
        source = self.save_evidence()
        updated = self.checked(self.client.patch('/api/runs/' + source['id'], json={
            'label': 'Before switch', 'notes': 'Observed in a disposable lab',
            'annotations': [{'seconds': 1.25, 'text': 'Synthetic timeline observation'}]}))
        self.assertEqual(len(updated['annotations']), 1)
        self.assertIn('+00:00', updated['annotations'][0]['timestamp'])
        source = self.manager.export(source['id'])
        imported = self.checked(self.upload('/api/runs/import', source))['imported'][0]
        self.assertEqual(self.checked(self.upload('/api/runs/import', source, xml=True))['duplicates'], [imported])
        exported = self.manager.export(imported)
        for key in ('settings', 'annotations', 'diagnostics', 'route', 'events', 'intervals', 'summary'):
            self.assertEqual(exported[key], source[key], key)
        self.restart()
        self.assertEqual(self.manager.export(imported), exported)

    def test_old_run_remains_without_invented_diagnostics_or_annotations(self):
        legacy = saved_run()
        imported = self.checked(self.upload('/api/runs/import', legacy))['imported'][0]
        restored = self.manager.export(imported)
        self.assertNotIn('diagnostics', restored)
        self.assertNotIn('annotations', restored)
        self.assertNotIn('latency', restored['settings'])
        self.assertEqual(restored['settings'], legacy['settings'])
        self.restart()
        self.assertEqual(self.manager.export(imported), restored)

    def test_project_baseline_bundle_remaps_ids_and_survives_actual_database_restart(self):
        project = self.project()
        before = self.save_evidence(project_id=project['id'])
        after = self.save_evidence('b' * 32, project['id'], 125e6)
        self.checked(self.client.patch('/api/projects/' + project['id'], json={'baseline_id': before['id']}))
        report = self.checked(self.client.get('/api/projects/' + project['id']))
        self.assertEqual(report['project']['baseline_id'], before['id'])
        self.assertEqual(report['comparisons'][0]['result'], 'improved')
        self.assertTrue(report['comparisons'][0]['comparable'])
        source = self.checked(self.client.get('/api/projects/' + project['id'] + '/export'))
        result = self.checked(self.upload('/api/projects/import', source))
        new_project = result['project']
        self.assertNotEqual(new_project['id'], project['id'])
        self.assertNotEqual(new_project['baseline_id'], before['id'])
        self.assertEqual(len(result['imported']), 2)
        for run_id in result['imported']:
            self.assertEqual(self.manager.read(run_id)['settings']['project_id'], new_project['id'])
        self.assertEqual(self.checked(self.upload('/api/projects/import', source, True))['duplicates'], [new_project['id']])
        imported_bundle = self.checked(self.client.get('/api/projects/' + new_project['id'] + '/export'))
        self.restart()
        self.assertEqual(self.checked(self.client.get('/api/projects/' + new_project['id'] + '/export')), imported_bundle)
        self.assertEqual(self.manager.export(after['id']), after)

    def test_project_import_second_write_failure_is_atomic(self):
        project = self.project()
        self.save_evidence(project_id=project['id'])
        self.save_evidence('b' * 32, project['id'])
        bundle = self.checked(self.client.get('/api/projects/' + project['id'] + '/export'))
        original_records = self.manager.storage.records()
        original_projects = self.manager.storage.metadata('projects')
        insert = self.manager.storage.insert_events
        calls = []
        def fail_second(*args):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('Synthetic disk failure')
            return insert(*args)
        with patch.object(self.manager.storage, 'insert_events', side_effect=fail_second):
            self.checked(self.upload('/api/projects/import', bundle), 503)
        self.assertEqual(self.manager.storage.records(), original_records)
        self.assertEqual(self.manager.storage.metadata('projects'), original_projects)
        self.restart()
        self.assertEqual(self.manager.storage.records(), original_records)

    def test_live_overlay_uses_elapsed_measured_points_and_flags_settings_mismatch(self):
        before = self.save_evidence()
        after = self.save_evidence('b' * 32)
        record = self.manager.read(after['id'])
        record.update(status='running', finished_at=None, intervals=[
            {'sum': {'start': 0, 'end': 1, 'bits_per_second': 80e6}},
            {'sum': {'start': 3, 'end': 4, 'bits_per_second': 90e6}}])
        record['settings']['streams'] = 2
        self.manager.save(record)
        overlay = self.checked(self.client.get('/api/live-comparison', query_string={
            'baseline': before['id'], 'active': after['id']}))
        self.assertIn('streams', overlay['settings_differences'])
        self.assertEqual([point['seconds'] for point in overlay['runs'][1]['series']], [1, 4])
        self.assertEqual([point['mbps'] for point in overlay['runs'][1]['series']], [80, 90])
        self.assertNotIn('diagnostics', overlay['runs'][1])
        self.assertEqual(overlay['runs'][1]['status'], 'running')


if __name__ == '__main__':
    unittest.main()
