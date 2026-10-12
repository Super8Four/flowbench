import copy
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from app import create_app
from app.comparisons import common_window
from app.imports import parse_document
from app.metrics import comparison
from app.runner import settings_from


def measured_run(index, duration=10, rate=100, exact=True):
    return {"schema_version": 1, "id": f'{index:032x}', "created_at": "2026-01-01T10:00:00+00:00",
            "finished_at": "2026-01-01T10:01:00+00:00", "status": "completed",
            "settings": settings_from({"target": "127.0.0.1", "duration": duration}),
            "iperf_version": "iperf 3.22", "output_format": "json", "exit_code": 0,
            "command": [], "error": None, "summary": {"sum_received": {"bits_per_second": rate * 1e6}},
            "intervals": [{"sum": dict({"end": end, "bits_per_second": rate * 1e6},
                                       **({"start": end - 1} if exact else {}))} for end in range(1, duration + 1)],
            "test_parameters": None, "event_count": 0}


class ManyComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": "missing-iperf"})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def store(self, count):
        runs = [measured_run(index + 1, 10 if index % 2 == 0 else 30, rate=100 + index) for index in range(count)]
        for run in runs:
            self.manager.save(run)
        return runs

    def url(self, runs, format_name=None):
        args = [('id', run['id']) for run in runs]
        if format_name:
            args.append(('format', format_name))
        return '/api/comparison?' + urlencode(args)

    def test_five_and_ten_runs_export_import_and_real_elapsed_duration(self):
        for count in (5, 10):
            runs = self.store(count)
            response = self.client.get(self.url(runs))
            self.assertEqual(response.status_code, 200, response.get_json())
            report = response.get_json()
            self.assertEqual(len(report['runs']), count)
            self.assertEqual(len(report['comparisons']), count - 1)
            self.assertEqual(report['common_window_seconds'], 10)
            self.assertEqual(report['aggregate_scope'], 'whole_run')
            self.assertEqual(max(point['seconds'] for point in report['runs'][0]['series']), 10)
            self.assertEqual(max(point['seconds'] for point in report['runs'][1]['series']), 30)
            self.assertEqual(report['runs'][1]['metrics']['upload_mbps'], 101)
            for format_name in ('json', 'xml'):
                exported = self.client.get(self.url(runs, format_name))
                self.assertEqual(exported.status_code, 200)
                parsed = parse_document(exported.data)
                self.assertEqual(len(parsed['runs']), count)
                imported = self.client.post('/api/runs/import', json={'filename': f'comparison-{count}.{format_name}',
                                                                       'content': exported.data.decode()})
                self.assertEqual(imported.status_code, 200, imported.get_json())

    def test_exact_common_window_is_separate_from_whole_run_aggregate(self):
        short = measured_run(1, 10, rate=100)
        long = measured_run(2, 30, rate=150)
        for index, row in enumerate(long['intervals']):
            row['sum']['bits_per_second'] = (50 if index < 10 else 200) * 1e6
        report = comparison(short, long)
        self.assertEqual(report['metrics'][0]['after'], 150)
        self.assertEqual(report['metrics'][0]['percent_change'], 50)
        window = report['common_window']
        self.assertEqual(window['seconds'], 10)
        self.assertEqual(window['runs'][0]['directions'][0]['mean_mbps'], 100)
        self.assertEqual(window['runs'][1]['directions'][0]['mean_mbps'], 50)
        self.assertIn('whole-run aggregates', window['note'])

    def test_unknown_starts_gaps_overlap_and_partial_bins_are_unavailable(self):
        short = measured_run(1, 10)
        variants = [measured_run(2, 30, exact=False)]
        gap = measured_run(2, 30); del gap['intervals'][3]; variants.append(gap)
        overlap = measured_run(2, 30); overlap['intervals'][3]['sum']['start'] = 2; variants.append(overlap)
        partial = measured_run(2, 30)
        partial['intervals'] = [{"sum": {"start": 0, "end": 15, "bits_per_second": 100e6}},
                                {"sum": {"start": 15, "end": 30, "bits_per_second": 100e6}}]
        variants.append(partial)
        for long in variants:
            window = common_window([short, long])
            self.assertEqual(window['seconds'], 10)
            self.assertEqual(window['runs'][1]['directions'][0]['status'], 'unavailable')
            self.assertIsNone(window['runs'][1]['directions'][0]['mean_mbps'])
        summary_only = measured_run(2); summary_only['intervals'] = []
        self.assertIsNone(common_window([short, summary_only])['seconds'])

    def test_duplicate_unfinished_parent_and_response_budget_reject(self):
        runs = self.store(5)
        self.assertEqual(self.client.get(self.url([runs[0], runs[0]])).status_code, 400)
        unfinished = copy.deepcopy(runs[-1]); unfinished['status'] = 'running'; self.manager.save(unfinished)
        self.assertEqual(self.client.get(self.url(runs)).status_code, 400)
        self.manager.save(runs[-1])
        parent = copy.deepcopy(runs[-1]); parent['kind'] = 'matrix-job'; self.manager.save(parent)
        self.assertEqual(self.client.get(self.url(runs)).status_code, 400)
        self.manager.save(runs[-1])
        with patch('app.comparisons.MAX_COMPARISON_BYTES', 100):
            response = self.client.get(self.url(runs))
        self.assertEqual(response.status_code, 413)
        self.assertIn('response budget', response.get_json()['error'])

    def test_comparison_reads_share_manager_snapshot_lock(self):
        runs = self.store(5)
        original_read, acquired = self.manager.read, []
        def checked_read(run_id):
            def try_lock():
                result = self.manager.lock.acquire(blocking=False)
                acquired.append(result)
                if result:
                    self.manager.lock.release()
            thread = threading.Thread(target=try_lock)
            thread.start(); thread.join(timeout=2)
            return original_read(run_id)
        with patch.object(self.manager, 'read', side_effect=checked_read):
            response = self.client.get(self.url(runs))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(acquired, [False] * 5)

    def test_persistent_colors_validated_restart_json_xml_and_import(self):
        run = self.store(2)[0]
        response = self.client.patch('/api/runs/' + run['id'], json={'color': '#Aa12EF'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['settings']['color'], '#aa12ef')
        for color in ('red', '#abcd', 'url(javascript:alert(1))', '#123456;', 2, {}):
            self.assertEqual(self.client.patch('/api/runs/' + run['id'], json={'color': color}).status_code, 400)
        self.assertEqual(self.manager.read(run['id'])['settings']['color'], '#aa12ef')
        for format_name in ('json', 'xml'):
            exported = self.client.get('/api/runs/' + run['id'] + '/export?format=' + format_name)
            value = parse_document(exported.data)
            self.assertEqual(value['settings']['color'], '#aa12ef')
            result = self.client.post('/api/runs/import', json={'filename': 'run.' + format_name, 'content': exported.data.decode()})
            self.assertEqual(result.status_code, 200, result.get_json())
            ids = result.get_json()['imported'] or result.get_json()['duplicates']
            self.assertEqual(self.manager.read(ids[0])['settings']['color'], '#aa12ef')
        self.manager.shutdown()
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name, "IPERF_PATH": "missing-iperf"})
        self.client = self.app.test_client(); self.manager = self.app.extensions['iperf_runs']
        self.assertEqual(self.manager.read(run['id'])['settings']['color'], '#aa12ef')
        cleared = self.client.patch('/api/runs/' + run['id'], json={'color': None})
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.get_json()['settings']['color'])

    def test_import_resource_budget_rejects_all_before_commit(self):
        runs = self.store(5)
        exported = self.client.get(self.url(runs, 'json'))
        before_count = len(self.manager.storage.records())
        with patch('app.imports.MAX_PREPARED_BYTES', 100):
            result = self.client.post('/api/runs/import', json={'filename': 'comparison.json', 'content': exported.data.decode()})
        self.assertEqual(result.status_code, 400)
        self.assertIn('import budget', result.get_json()['error'])
        self.assertEqual(len(self.manager.storage.records()), before_count)

    def test_large_finite_rate_window_and_packet_volume_not_improvement(self):
        short, long = measured_run(1, 10), measured_run(2, 30)
        for row in long['intervals']:
            row['sum']['bits_per_second'] = 1e308
        window = common_window([short, long])
        self.assertEqual(window['runs'][1]['directions'][0]['status'], 'complete')
        self.assertAlmostEqual(window['runs'][1]['directions'][0]['mean_mbps'] / 1e302, 1)
        short['summary']['sum_received']['packets'] = 100
        long['summary']['sum_received']['packets'] = 300
        packet = next(row for row in comparison(short, long)['metrics'] if row['metric'] == 'upload_packets')
        self.assertEqual(packet['direction'], 'volume change')


if __name__ == '__main__':
    unittest.main()
