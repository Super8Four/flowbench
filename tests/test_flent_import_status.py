"""Portable status claims cannot turn incomplete native RRUL into success."""

import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from app.exports import xml_export
from app.flent import normalize, RRUL_TCP
try:
    from test_flent import native, payload
except ModuleNotFoundError:
    from tests.test_flent import native, payload


class FlentImportStatusTests(unittest.TestCase):
    def test_completed_wrapper_rejected_but_partial_evidence_imports_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            manager = app.extensions['iperf_runs']
            try:
                manager.flent_capabilities = {'available': True, 'version': 'Flent fixture',
                                              'dependencies': {'flent': '/controlled/unused'}}
                with patch.object(manager, 'run_flent'):
                    record = manager.start_flent(payload())
                    manager.worker.join(5)
                source = native()
                for name in RRUL_TCP:
                    source['results'][name] = [None] * len(source['x_values'])
                record.update(status='partial', finished_at=record['created_at'], flent=normalize(source))
                client = app.test_client()
                for xml in (False, True):
                    broken = copy.deepcopy(record)
                    broken['status'] = 'completed'
                    content = xml_export(broken).decode() if xml else json.dumps(broken)
                    before = len(manager.list_runs())
                    response = client.post('/api/runs/import', json={'content': content, 'filename': 'missing.xml' if xml else 'missing.json'})
                    self.assertEqual(response.status_code, 400, response.get_json())
                    self.assertIn('completed Flent', response.get_json()['error'])
                    self.assertEqual(len(manager.list_runs()), before)
                response = client.post('/api/runs/import', json={'content': json.dumps(record), 'filename': 'partial.json'})
                self.assertEqual(response.status_code, 200, response.get_json())
                restored = manager.export(response.get_json()['imported'][0])
                self.assertEqual(restored['status'], 'partial')
                self.assertEqual(restored['flent']['data'], source)
                report = client.post('/api/reports/preview', json={'ids': [restored['id']]})
                self.assertEqual(report.status_code, 400)
            finally:
                manager.shutdown()
