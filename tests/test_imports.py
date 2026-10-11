import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from app.exports import xml_export
from app.imports import parse_document
from app.runner import settings_from


def saved_run():
    return {"schema_version": 1, "id": "a" * 32, "created_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:00:10+00:00", "status": "completed",
            "settings": settings_from({"target": "office.example", "notes": "Before\nfix"}),
            "iperf_version": "iperf 3.22", "output_format": "json", "exit_code": 0,
            "command": ["iperf3", "-c", "office.example"], "error": None,
            "summary": {"sum_received": {"bits_per_second": 100e6}, "sum_sent": {"retransmits": 5}},
            "intervals": [{"sum": {"end": 1, "bits_per_second": 100e6}}], "test_parameters": None,
            "native_result": {"arbitrary_original": [True, None, "café"]},
            "events": [{"sequence": 1, "timestamp": "2026-01-01T10:00:00+00:00",
                        "source": "stdout", "message": "original log <xml>\x1b"}]}


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name,
                               "IPERF_PATH": "missing-iperf", "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                               "SQLALCHEMY_ENGINE_OPTIONS": {}})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["iperf_runs"]
        self.presets = self.app.extensions["iperf_presets"]

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_readiness_requires_engine_and_working_archive_without_database(self):
        self.assertEqual(self.client.get('/api/ready').status_code, 503)
        self.manager.capabilities['available'] = True
        self.assertEqual(self.client.get('/api/ready').get_json(), {'ready': True})
        self.manager.storage_error = 'Disk full'
        self.assertEqual(self.client.get('/api/ready').status_code, 503)
        self.manager.storage_error = None
        self.manager.shutdown()
        self.assertEqual(self.client.get('/api/ready').status_code, 503)

    def upload(self, endpoint, data, xml=False):
        content = xml_export(data).decode() if xml else json.dumps(data)
        return self.client.post(endpoint, json={"filename": "old-test.xml" if xml else "old-test.json", "content": content})

    def test_json_xml_restore_logs_metrics_and_skip_duplicates(self):
        source = saved_run()
        for xml in (False, True):
            response = self.upload('/api/runs/import', source, xml)
            self.assertEqual(response.status_code, 200, response.get_json())
            if not xml:
                run_id = response.get_json()['imported'][0]
                restored = self.manager.export(run_id)
                self.assertNotEqual(run_id, source['id'])
                for field in ('created_at', 'finished_at', 'settings', 'summary', 'events', 'native_result', 'intervals'):
                    self.assertEqual(restored[field], source[field])
                self.assertEqual(restored['metrics']['upload_mbps'], 100)
            else:
                self.assertEqual(response.get_json()['duplicates'], [run_id])
        self.assertEqual(len(self.manager.list_runs()), 1)
        self.assertIsNone(self.manager.active)

    def test_comparison_import_retains_both_and_is_comparable(self):
        before = saved_run(); after = copy.deepcopy(before)
        after['id'] = 'b' * 32
        after['summary']['sum_received']['bits_per_second'] = 200e6
        result = self.upload('/api/runs/import', {'schema_version': 1, 'before': before, 'after': after}, xml=True)
        self.assertEqual(result.status_code, 200, result.get_json())
        left, right = result.get_json()['imported']
        report = self.client.get(f'/api/comparison?before={left}&after={right}').get_json()
        throughput = next(r for r in report['metrics'] if r['metric'] == 'upload_mbps')
        self.assertEqual(throughput['percent_change'], 100)
        self.assertFalse(report['settings_differences'])

    def test_native_json_retains_original_date_parameters_and_results(self):
        source = {'start': {'version': 'iperf 3.22', 'timestamp': {'timesecs': 1767261600},
                            'connecting_to': {'host': 'office.example', 'port': 5202},
                            'test_start': {'protocol': 'UDP', 'num_streams': 2, 'duration': 10, 'reverse': 1, 'target_bitrate': 5e6}},
                  'intervals': [{'sum': {'end': 1, 'bits_per_second': 4e6}}],
                  'end': {'sum_received': {'bits_per_second': 4e6, 'lost_percent': 1, 'jitter_ms': .5}}}
        response = self.upload('/api/runs/import', source)
        self.assertEqual(response.status_code, 200, response.get_json())
        record = self.manager.export(response.get_json()['imported'][0])
        self.assertEqual(record['native_result'], source)
        self.assertEqual(record['metrics']['download_mbps'], 4)
        self.assertEqual(record['settings']['target'], 'office.example')
        self.assertEqual(record['effective_settings']['bandwidth'], 5e6)
        self.assertTrue(record['created_at'].startswith('2026-01-01'))

    def test_invalid_comparison_is_validated_before_any_write(self):
        broken = saved_run(); broken['summary']['sum_received']['bits_per_second'] = 'fake'
        response = self.upload('/api/runs/import', {'before': saved_run(), 'after': broken})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.manager.list_runs())
        for field, value in (('events', [{'sequence': 2}]), ('intervals', [{'sum': {'bits_per_second': 3}}]),
                             ('test_parameters', {'protocol': []}), ('status', []), ('created_at', 'bad')):
            broken = saved_run(); broken[field] = value
            with self.subTest(field=field):
                self.assertEqual(self.upload('/api/runs/import', broken).status_code, 400)

    def test_snapshots_are_interrupted_and_never_started(self):
        source = saved_run(); source.update(status='running', finished_at=None)
        result = self.upload('/api/runs/import', source).get_json()
        restored = self.manager.read(result['imported'][0])
        self.assertEqual(restored['status'], 'interrupted')
        self.assertIsNone(self.manager.active)
        self.assertIsNone(self.manager.worker)

    def test_reject_entities_nonfinite_bad_shapes_and_cross_origin(self):
        for content in ('<!DOCTYPE iperf_run [<!ENTITY a "expansion">]><iperf_run type="string">&a;</iperf_run>',
                        '{"rate": NaN}', '{"rate": 1e400}', '<iperf_run type="boolean">maybe</iperf_run>',
                        '<iperf_run type="number">true</iperf_run>', '[]'):
            with self.subTest(content=content):
                result = self.client.post('/api/runs/import', json={'content': content})
                self.assertEqual(result.status_code, 400)
        self.assertEqual(self.client.post('/api/runs/import', json={'content': '{}'}, headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/runs/import', data='{}').status_code, 415)

    def test_public_catalog_and_official_formats_port_ranges_and_flags(self):
        catalog = self.client.get('/api/presets').get_json()
        self.assertGreater(len(catalog['presets']), 100)
        row = {'IP/HOST': 'public.example', 'PORT': '5200-5209', 'OPTIONS': '-R,-6,-u',
               'COUNTRY': 'US', 'SITE': 'Chicago', 'CONTINENT': 'North America', 'PROVIDER': 'Example'}
        result = self.upload('/api/presets/import', [row])
        self.assertEqual(result.status_code, 200, result.get_json())
        stored = next(r for r in self.presets.read()['presets'] if r['settings']['target'] == 'public.example')
        self.assertEqual(stored['ports'], '5200-5209')
        self.assertEqual(stored['settings']['port'], 5200)
        self.assertTrue(stored['ipv6'])
        self.assertEqual(stored['protocols'], ['tcp', 'udp'])
        raw = {**row, 'COMMAND': 'iperf3 -c raw.example -p 6001-6003'}; del raw['IP/HOST']; del raw['PORT']
        self.assertEqual(self.upload('/api/presets/import', [raw]).status_code, 200)
        download = self.client.get('/api/presets?download=1')
        self.assertIn('attachment', download.headers['Content-Disposition'])
        count = len(self.presets.read()['presets'])
        self.assertEqual(self.upload('/api/presets/import', download.get_json()).status_code, 200)
        self.assertEqual(len(self.presets.read()['presets']), count)

    def test_custom_preset_edit_persistence_and_removal(self):
        data = {'name': 'My office', 'ports': '5201-5209', 'notes': 'Private lab',
                'settings': settings_from({'target': 'office.example', 'protocol': 'udp', 'bandwidth': '5M',
                                           'ip_version': '6', 'mode': 'download', 'streams': 3})}
        result = self.client.post('/api/presets', json=data)
        self.assertEqual(result.status_code, 201)
        item = result.get_json()
        data['settings']['duration'] = 20
        result = self.client.patch('/api/presets/' + item['id'], json=data)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['settings']['duration'], 20)
        self.assertIn('-6', self.manager.command(result.get_json()['settings']))
        self.assertTrue((Path(self.temp.name) / 'server-presets.json').is_file())
        self.assertEqual(self.client.delete('/api/presets/' + item['id'], json={}).status_code, 200)
        public = self.presets.read()['presets'][0]
        self.assertEqual(self.client.delete('/api/presets/' + public['id'], json={}).status_code, 400)

    def test_bad_preset_file_does_not_modify_catalog_or_execute_commands(self):
        original = self.presets.read()
        valid = {'IP/HOST': 'public.example', 'PORT': '5201', 'OPTIONS': '-R'}
        for broken in ({**valid, 'PORT': '65536'}, {**valid, 'IP/HOST': 'host;whoami'},
                       {**valid, 'PORT': '6000-5000'}, {'COMMAND': 'iperf3 -c host -p 5201;whoami'},
                       {**valid, 'OPTIONS': '-R --debug'}):
            with self.subTest(broken=broken):
                self.assertEqual(self.upload('/api/presets/import', [valid, broken]).status_code, 400)
                self.assertEqual(self.presets.read(), original)

    def test_unicode_source_matches_bundled_catalog_without_duplicates(self):
        original = self.presets.read()
        source = [{'IP/HOST': 'speedtest.iway.ch', 'PORT': '5201', 'OPTIONS': '-R,-6,-u',
                   'COUNTRY': 'CH', 'SITE': 'Zürich', 'PROVIDER': 'iWay', 'CONTINENT': 'Europe', 'GB/S': '10'}]
        result = self.upload('/api/presets/import', source)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json()['added'], 0)
        self.assertEqual(len(self.presets.read()['presets']), len(original['presets']))

    def test_refresh_preserves_custom_and_failure_retains_catalog(self):
        self.client.post('/api/presets', json={'name': 'Office', 'settings': {'target': 'office.example'}})
        source = [{'IP/HOST': 'public.example', 'PORT': '5201', 'OPTIONS': '-R'}]
        with patch('app.presets.urllib.request.urlopen') as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = json.dumps(source).encode()
            response = self.client.post('/api/presets/refresh', json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {'public': 1, 'custom': 1})
        original = self.presets.read()
        with patch('app.presets.urllib.request.urlopen', side_effect=OSError('Offline')):
            self.assertEqual(self.client.post('/api/presets/refresh', json={}).status_code, 502)
        self.assertEqual(self.presets.read(), original)


if __name__ == '__main__':
    unittest.main()
