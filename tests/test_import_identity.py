import copy
import json
import tempfile
import unittest

from app import create_app
from app.exports import xml_export
from app.metrics import measurements, overview, series
from app.runner import settings_from


def saved_identity_run():
    return {"schema_version": 1, "id": "a" * 32, "created_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:00:10+00:00", "status": "completed",
            "settings": settings_from({"target": "127.0.0.1"}), "iperf_version": "iperf 3.22",
            "output_format": "text", "exit_code": 0, "command": [], "error": None,
            "summary": {"sum_received": {"bits_per_second": 100e6}},
            "intervals": [{"start": 0, "end": 1, "bits_per_second": 100e6, "direction": "upload", "stream": "SUM"}],
            "test_parameters": None, "event_count": 0, "events": []}


class ImportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": "missing-iperf"})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def upload(self, data, xml=False):
        content = xml_export(data).decode() if xml else json.dumps(data)
        return self.client.post('/api/runs/import', json={'filename': 'identity.xml' if xml else 'identity.json', 'content': content})

    def test_direction_and_stream_shape_errors_json_xml_never_commit(self):
        invalid = {'direction': [{"unexpected": "object"}, [], True, 7, "", "x" * 201],
                   'stream': [{"unexpected": "object"}, [], True, -1, 1.5, None, "", "-2", 2**40, "9" * 100, "x" * 201]}
        for field, values in invalid.items():
            for value in values:
                for xml in (False, True):
                    source = saved_identity_run(); source['intervals'][0][field] = value
                    response = self.upload(source, xml)
                    self.assertEqual(response.status_code, 400, (field, value, xml, response.get_json()))
                    self.assertIn('identity', response.get_json()['error'])
                    self.assertEqual(self.manager.storage.records(), [])

    def test_nested_summary_identity_and_native_json_are_validated(self):
        source = saved_identity_run()
        source['summary']['text_summaries'] = {'receiver': [{"stream": ["SUM"], "end": 1, "bits_per_second": 100e6}]}
        for xml in (False, True):
            self.assertEqual(self.upload(source, xml).status_code, 400)
        native = {'start': {'version': 'iperf 3.22', 'timestamp': {'timesecs': 1767261600},
                            'connecting_to': {'host': '127.0.0.1', 'port': 5201},
                            'test_start': {'protocol': 'TCP', 'duration': 10, 'num_streams': 1}},
                  'intervals': [{'sum': {'start': 0, 'end': 1, 'bits_per_second': 100e6, 'stream': False}}],
                  'end': {'sum_received': {'bits_per_second': 100e6}}}
        self.assertEqual(self.upload(native).status_code, 400)
        self.assertEqual(self.manager.storage.records(), [])

    def test_batch_validation_rolls_back_valid_run_before_poison(self):
        good = saved_identity_run(); bad = copy.deepcopy(good); bad['id'] = 'b' * 32
        bad['intervals'][0]['direction'] = {'unexpected': 'object'}
        envelope = {'schema_version': 1, 'kind': 'multi-comparison', 'runs': [good, bad]}
        for xml in (False, True):
            self.assertEqual(self.upload(envelope, xml).status_code, 400)
            self.assertEqual(self.manager.storage.records(), [])

    def test_legitimate_scalar_identities_preserved_verbatim(self):
        for stream in ('SUM', '4', 7, 0, 'custom <stream>'):
            source = saved_identity_run()
            source['intervals'][0].update(direction='custom <direction>', stream=stream)
            source['summary']['text_summaries'] = {'receiver': [{"stream": stream, "end": 1, "bits_per_second": 100e6}]}
            response = self.upload(source)
            self.assertEqual(response.status_code, 200, response.get_json())
            restored = self.manager.read(response.get_json()['imported'][0])
            self.assertEqual(restored['intervals'], source['intervals'])
            self.assertEqual(restored['summary'], source['summary'])
            self.assertEqual(series(restored)[0]['direction'], 'custom <direction>')
        optional = saved_identity_run(); optional['intervals'][0]['direction'] = None
        self.assertEqual(self.upload(optional).status_code, 200)

    def test_historical_poison_remains_visible_and_raw_unchanged_after_restart(self):
        source = saved_identity_run(); source.pop('events')
        source['intervals'][0]['direction'] = {'unexpected': 'object'}
        self.manager.save(source)
        good = saved_identity_run(); good.pop('events'); good['id'] = 'b' * 32; self.manager.save(good)
        before = self.manager.storage.read(source['id'])
        self.assertEqual(series(source), [])
        self.assertEqual(measurements(source), {'upload_mbps': 100})
        self.assertEqual(overview(source)['status'], 'unavailable')
        for path in ('/api/runs', '/api/runs/' + source['id'], '/api/runs/' + source['id'] + '/export'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, response.get_json())
            if path == '/api/runs':
                rows = response.get_json()['runs']
                self.assertEqual(len(rows), 2)
                row = next(row for row in rows if row['id'] == source['id'])
                self.assertTrue(row['measurement_warnings'])
                self.assertEqual(row['overview']['status'], 'unavailable')
            else:
                row = response.get_json()
                self.assertEqual(row['intervals'], source['intervals'])
                self.assertEqual(row['series'], [])
                self.assertTrue(row['overview']['issues'])
        self.assertEqual(self.manager.storage.read(source['id']), before)
        self.manager.shutdown()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": "missing-iperf"})
        self.client = self.app.test_client(); self.manager = self.app.extensions['iperf_runs']
        self.assertEqual(self.client.get('/api/runs').status_code, 200)
        self.assertEqual(self.manager.storage.read(source['id']), before)

    def test_historical_bad_summary_stream_does_not_fabricate_aggregate(self):
        source = saved_identity_run()
        source['summary'] = {'text_summaries': {'receiver': [{"stream": {}, "bits_per_second": 100e6, "end": 1}]}}
        self.assertEqual(measurements(source), {})
        self.assertEqual(series(source), [])
        self.assertEqual(overview(source)['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
