import copy
import tempfile
import unittest

from app import create_app
from app.metrics import live_comparison, series
from test_imports import saved_run


class LiveComparisonTests(unittest.TestCase):
    def test_text_parallel_streams_keep_common_interval_boundaries(self):
        record = saved_run()
        record['output_format'] = 'text'
        record['intervals'] = [
            {'start': 0, 'end': 1, 'bits_per_second': 3e6, 'stream': '4'},
            {'start': 0, 'end': 1, 'bits_per_second': 2e6, 'stream': '5'},
            {'start': 3, 'end': 4, 'bits_per_second': 4e6, 'stream': '4'}]
        self.assertEqual(series(record), [
            {'start': 0, 'seconds': 1, 'mbps': 5, 'direction': 'upload'},
            {'start': 3, 'seconds': 4, 'mbps': 4, 'direction': 'upload'}])
        record['intervals'][1]['start'] = 0.5
        self.assertNotIn('start', series(record)[0])

    def test_elapsed_time_preserves_gaps_and_absent_metrics(self):
        baseline = saved_run()
        baseline['intervals'] = [
            {'sum': {'start': 0, 'end': 1, 'bits_per_second': 10e6}},
            {'sum': {'start': 3, 'end': 4, 'bits_per_second': 20e6}}]
        active = copy.deepcopy(baseline)
        active.update(id='b' * 32, status='running', summary={}, finished_at=None,
                      created_at='2026-02-01T10:00:00+00:00')
        active['intervals'] = [{'sum': {'start': 0, 'end': 1, 'bits_per_second': 12e6}}]
        report = live_comparison(baseline, active)
        self.assertEqual(report['runs'][0]['series'], [
            {'start': 0, 'seconds': 1, 'mbps': 10, 'direction': 'upload'},
            {'start': 3, 'seconds': 4, 'mbps': 20, 'direction': 'upload'}])
        self.assertEqual(report['runs'][1]['metrics'], {})
        self.assertEqual(len(report['runs'][1]['series']), 1)
        self.assertEqual(report['settings_differences'], [])

    def test_flags_peer_direction_streams_and_engine_differences(self):
        baseline = saved_run()
        active = copy.deepcopy(baseline)
        active['id'] = 'b' * 32
        active['settings'].update(target='127.0.0.1', mode='download', streams=2, protocol='udp', bandwidth='2M')
        active['iperf_version'] = 'iperf 3.21'
        self.assertEqual(set(live_comparison(baseline, active)['settings_differences']),
                         {'target', 'mode', 'streams', 'protocol', 'bandwidth', 'iperf_version'})

    def test_rejects_missing_baseline_measurements_same_identity_and_groups(self):
        baseline, active = saved_run(), saved_run()
        active['id'] = 'b' * 32
        for mutation in (lambda b: b.update(status='failed'), lambda b: b.update(summary={}),
                         lambda b: b.update(kind='matrix-job'), lambda b: b.update(id=active['id'])):
            bad = copy.deepcopy(baseline)
            mutation(bad)
            with self.assertRaises(ValueError):
                live_comparison(bad, active)
        active['kind'] = 'protocol-job'
        with self.assertRaises(ValueError):
            live_comparison(baseline, active)

    def test_api_reads_current_interval_snapshot_and_returns_validation_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            manager = app.extensions['iperf_runs']
            try:
                baseline, active = saved_run(), saved_run()
                active.update(id='b' * 32, status='running', summary={}, finished_at=None)
                manager.save(baseline)
                manager.save(active)
                manager.active, manager.record = active['id'], active
                client = app.test_client()
                url = '/api/live-comparison?baseline=' + baseline['id'] + '&active=' + active['id']
                response = client.get(url)
                self.assertEqual(response.status_code, 200)
                manager.record['intervals'].append({'sum': {'start': 2, 'end': 3, 'bits_per_second': 50e6}})
                self.assertEqual(len(client.get(url).get_json()['runs'][1]['series']), 2)
                self.assertEqual(client.get('/api/live-comparison?baseline=invalid&active=' + active['id']).status_code, 404)
                self.assertEqual(client.get('/api/live-comparison?baseline=' + active['id'] + '&active=' + baseline['id']).status_code, 400)
            finally:
                manager.active = None
                manager.shutdown()
