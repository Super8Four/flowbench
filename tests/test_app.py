import base64
import copy
import json
import os
from pathlib import Path
import socket
import secrets
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from app import create_app
from app.exports import xml_export
from app.metrics import comparison, measurements, series
from app.runner import CREATE_FLAGS, RunManager, TERMINAL, settings_from


def decode_xml(node):
    kind = node.attrib['type']
    if kind == 'object': return {child.attrib['name']: decode_xml(child) for child in node}
    if kind == 'array': return [decode_xml(child) for child in node]
    if kind == 'null': return None
    if kind == 'boolean': return node.text == 'true'
    if kind == 'number': return json.loads(node.text)
    if node.get('encoding') == 'base64': return base64.b64decode(node.text).decode('utf-8')
    return node.text or ''


class CoreTests(unittest.TestCase):
    def test_validation_rejects_arguments_and_noninteger_numbers(self):
        for data in ({'target': '-s'}, {'target': 'host;whoami'}, {'target': 'host name'},
                     {'target': 'host', 'port': True}, {'target': 'host', 'duration': '1.5'},
                     {'target': 'host', 'streams': 0}, {'target': 'host', 'bandwidth': '10M;id'},
                     {'target': 'host', 'mode': []}, {'target': 'host', 'debug': 'true'}):
            with self.subTest(data=data), self.assertRaises(ValueError): settings_from(data)
        self.assertEqual(settings_from({'target': '::1'})['target'], '::1')
        self.assertEqual(settings_from({'role': 'server'})['role'], 'server')

    def test_xml_roundtrip_preserves_types_and_logs(self):
        data = {'end': {'sum': 12.5, 'nested': [True, False, None, {}, [], 0]},
                'raw_stdout': '<error> & "quotes"\nUnicode: café', 'control': '\x00\x1b',
                'empty': '', 'settings': {'notes': 'before\nafter'}}
        self.assertEqual(decode_xml(ET.fromstring(xml_export(data))), data)

    def test_metrics_use_receiver_rate_and_preserve_absent_values(self):
        record = {'settings': settings_from({'target': 'host'}), 'summary': {
            'sum_sent': {'bits_per_second': 110_000_000, 'retransmits': 7},
            'sum_received': {'bits_per_second': 100_000_000}}}
        metrics = measurements(record)
        self.assertEqual(metrics['upload_mbps'], 100)
        self.assertEqual(metrics['upload_retransmits'], 7)
        self.assertNotIn('upload_jitter_ms', metrics)
        record['settings']['mode'] = 'download'
        self.assertEqual(measurements(record)['download_mbps'], 100)

    def test_server_bidir_and_parallel_streams_are_not_mixed(self):
        record = {'settings': settings_from({'role': 'server'}),
                  'test_parameters': {'protocol': 'TCP', 'bidir': 1, 'num_streams': 2, 'duration': 2},
                  'summary': {'sum_received': {'bits_per_second': 100e6},
                              'sum_sent_bidir_reverse': {'bits_per_second': 200e6}},
                  'intervals': [{'sum': {'end': 1, 'bits_per_second': 100e6},
                                 'sum_bidir_reverse': {'end': 1, 'bits_per_second': 200e6},
                                 'streams': [{'end': 1, 'bits_per_second': 50e6}]}]}
        self.assertEqual(measurements(record), {'upload_mbps': 100, 'download_mbps': 200})
        self.assertEqual(len(series(record)), 2)
        self.assertEqual(series(record)[0]['direction'], 'upload')

    def test_comparison_zero_baseline_and_settings_mismatch(self):
        before = {'id': 'a', 'status': 'completed', 'iperf_version': 'test',
                  'settings': settings_from({'target': 'host'}),
                  'summary': {'sum_received': {'bits_per_second': 0}, 'sum_sent': {'retransmits': 5}}}
        after = copy.deepcopy(before)
        after['id'] = 'b'; after['settings']['duration'] = 20
        after['summary']['sum_received']['bits_per_second'] = 100e6
        after['summary']['sum_sent']['retransmits'] = 0
        report = comparison(before, after)
        rate = next(row for row in report['metrics'] if row['metric'] == 'upload_mbps')
        retransmits = next(row for row in report['metrics'] if row['metric'].endswith('retransmits'))
        self.assertIsNone(rate['percent_change'])
        self.assertEqual(retransmits['direction'], 'improved')
        self.assertEqual(report['settings_differences'], ['duration'])
        with self.assertRaises(ValueError): comparison(before, before)

    def test_startup_failure_and_restart_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory, executable='this-binary-does-not-exist')
            manager.capabilities['available'] = True
            record = manager.start({'target': '127.0.0.1', 'mode': 'upload'})
            manager.worker.join(5)
            self.assertEqual(manager.read(record['id'])['status'], 'failed')
            exported = manager.export(record['id'])
            self.assertTrue(exported['events'])
            manager.shutdown()
            reopened = RunManager(directory, executable='this-binary-does-not-exist')
            try:
                self.assertEqual(reopened.export(record['id']), exported)
            finally: reopened.shutdown()

    def test_metadata_failure_retains_previous_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory, executable='this-binary-does-not-exist')
            try:
                manager.save({'id': 'b' * 32, 'status': 'completed'})
                with patch.object(manager.storage, 'save', side_effect=OSError('Disk full')):
                    with self.assertRaises(OSError):
                        manager.save({'id': 'b' * 32, 'status': 'failed'})
                self.assertEqual(manager.read('b' * 32)['status'], 'completed')
            finally: manager.shutdown()

    def test_recovery_marks_unfinished_sessions_and_prevents_two_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory, executable='this-binary-does-not-exist')
            with self.assertRaises(RuntimeError): RunManager(directory, executable='this-binary-does-not-exist')
            record = {'id': 'a' * 32, 'status': 'running', 'finished_at': None}
            manager.save(record); manager.shutdown()
            reopened = RunManager(directory, executable='this-binary-does-not-exist')
            try:
                self.assertEqual(reopened.read(record['id'])['status'], 'interrupted')
                self.assertIsNotNone(reopened.read(record['id'])['finished_at'])
            finally: reopened.shutdown()


IPERF = RunManager.find_executable()


@unittest.skipUnless(IPERF, 'Install iPerf3 to run the loopback integration tests.')
class RealIperfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name, 'IPERF_PATH': IPERF})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']
        self.peers = []

    def tearDown(self):
        self.manager.shutdown()
        for peer in self.peers:
            if peer.poll() is None: peer.kill()
            peer.communicate()
        self.temp.cleanup()

    def port(self):
        # Windows can reserve a UDP range independently of TCP. iPerf's UDP
        # listener shares the TCP control port, so verify both before selecting it.
        for _ in range(100):
            with socket.socket() as tcp, socket.socket(type=socket.SOCK_DGRAM) as udp:
                # Windows may repeatedly allocate the same ephemeral UDP port
                # while TCP still has it in TIME_WAIT. Probe distinct random
                # candidates without modifying any host port reservations.
                port = 20000 + secrets.randbelow(45535)
                try:
                    udp.bind(('127.0.0.1', port))
                    tcp.bind(('127.0.0.1', port))
                except OSError:
                    continue
                return port
        self.fail('No available loopback TCP/UDP test port found')

    def server(self):
        port = self.port()
        peer = subprocess.Popen([IPERF, '-s', '-1', '-B', '127.0.0.1', '-p', str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
        self.peers.append(peer)
        time.sleep(.4)
        return port

    def finish(self, run_id, timeout=None):
        data = self.client.get(f'/api/runs/{run_id}').get_json()
        if timeout is None:
            # A native route can legitimately consume 20 seconds, followed by
            # a three-second clock query, before traffic starts. Each Both child
            # has its own budget; preserve the actual completion assertions.
            count = max(1, len(data.get('children', [])))
            timeout = count * (23 + data['settings']['duration'] + 10)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.client.get(f'/api/runs/{run_id}').get_json()
            if data['status'] in TERMINAL and data['finished_at']:
                self.manager.worker.join(2)
                return data
            time.sleep(.05)
        self.fail('iPerf session did not reach a terminal status: ' + json.dumps({
            'status': data.get('status'), 'preflight_status': data.get('preflight', {}).get('status'),
            'route_status': data.get('route', {}).get('status'), 'route_seconds': data.get('route', {}).get('elapsed_seconds'),
            'clock_status': data.get('preflight', {}).get('time_quality', {}).get('status')}))

    def run_test(self, **settings):
        data = {'target': '127.0.0.1', 'port': self.server(), 'duration': 2, 'mode': 'upload', 'label': 'Loopback verification', **settings}
        response = self.client.post('/api/runs', json=data)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['id']

    def test_tcp_live_output_exports_annotation_and_restart(self):
        # Leave enough time for the Windows subprocess to start and flush a
        # full interval while still proving output arrives before completion.
        run_id = self.run_test(streams=2, duration=4)
        deadline = time.monotonic() + 28  # Native preflight budget plus the first live interval.
        live = None
        while time.monotonic() < deadline:
            candidate = self.client.get(f'/api/runs/{run_id}').get_json()
            if candidate['status'] == 'running' and candidate['series']:
                live = candidate; break
            time.sleep(.05)
        self.assertIsNotNone(live, 'At least one interval must arrive before the session finishes')
        result = self.finish(run_id)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertGreater(result['metrics']['upload_mbps'], 0)
        self.assertGreaterEqual(len(result['series']), 2)
        self.assertEqual(result['overview']['max_upload_mbps'], max(point['mbps'] for point in result['series']))
        self.assertAlmostEqual(result['overview']['runtime_seconds'], 4, delta=.5)
        history = self.client.get('/api/runs').get_json()['runs']
        self.assertEqual(next(row for row in history if row['id'] == run_id)['overview'], result['overview'])
        self.assertIn('start', result['native_result'])
        self.assertIn('server_output_text', result['native_result'])
        self.assertTrue(result['peer']['available'])
        self.assertTrue(result['peer']['series'])
        self.assertGreater(result['peer']['metrics']['upload_mbps'], 0)
        seq = result['events'][0]['sequence']
        replay = self.client.get(f'/api/runs/{run_id}?after={seq}').get_json()['events']
        self.assertTrue(all(event['sequence'] > seq for event in replay))
        response = self.client.patch(f'/api/runs/{run_id}', json={'notes': 'Switch replaced & verified <ok>', 'label': 'After fix'})
        self.assertEqual(response.status_code, 200)
        json_response = self.client.get(f'/api/runs/{run_id}/export?format=json')
        xml_response = self.client.get(f'/api/runs/{run_id}/export?format=xml')
        self.assertEqual(decode_xml(ET.fromstring(xml_response.data)), json_response.get_json())
        self.assertIn('attachment', json_response.headers['Content-Disposition'])
        exported = json_response.get_json()
        self.assertEqual(exported['overview'], result['overview'])
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, IPERF)
        self.assertEqual(self.manager.export(run_id), exported)

    def test_download_and_bidirectional_metrics(self):
        for mode in ['download', 'bidir']:
            with self.subTest(mode=mode):
                result = self.finish(self.run_test(mode=mode))
                self.assertEqual(result['status'], 'completed', result.get('error'))
                self.assertGreater(result['metrics']['download_mbps'], 0)
                if mode == 'bidir': self.assertGreater(result['metrics']['upload_mbps'], 0)

    def test_udp_loss_jitter_and_comparison_export(self):
        before_id = self.run_test(protocol='udp', bandwidth='5M')
        before = self.finish(before_id)
        self.assertEqual(before['status'], 'completed', before.get('error'))
        self.assertIn('upload_lost_percent', before['metrics'])
        self.assertIn('upload_jitter_ms', before['metrics'])
        after_id = self.run_test(protocol='udp', bandwidth='5M')
        after = self.finish(after_id)
        self.assertEqual(after['status'], 'completed', after.get('error'))
        query = f'/api/comparison?before={before_id}&after={after_id}'
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200, response.get_json())
        report = response.get_json()
        self.assertEqual(report['settings_differences'], ['port'])
        json_report = self.client.get(query + '&format=json').get_json()
        xml_report = decode_xml(ET.fromstring(self.client.get(query + '&format=xml').data))
        self.assertEqual(xml_report, json_report)
        self.assertTrue(json_report['before']['events'])
        self.assertTrue(json_report['after']['events'])

    def test_cancel_retains_output_and_prevents_overlap(self):
        run_id = self.run_test(duration=20)
        response = self.client.post('/api/runs', json={'target': '127.0.0.1'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.post(f'/api/runs/{run_id}/stop', json={}).status_code, 200)
        result = self.finish(run_id)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(result['events'])

    def test_connection_failure_is_retained(self):
        response = self.client.post('/api/runs', json={'target': '127.0.0.1', 'port': self.port(), 'duration': 1})
        result = self.finish(response.get_json()['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['error'])
        self.assertTrue(self.client.get(f"/api/runs/{result['id']}/export").get_json()['raw_stdout'])

    def test_output_capture_failure_never_reports_success(self):
        original = self.manager.log
        def denied(source, line):
            if source == 'stdout': raise OSError('Simulated log archive failure')
            return original(source, line)
        with patch.object(self.manager, 'log', side_effect=denied):
            result = self.finish(self.run_test())
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Output capture failed', result['error'])
        self.assertFalse(self.manager.active)

    def test_server_session_uses_actual_peer_parameters(self):
        port = self.port()
        response = self.client.post('/api/runs', json={'role': 'server', 'bind': '127.0.0.1', 'port': port, 'idle_timeout': 10})
        run_id = response.get_json()['id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.client.get(f'/api/runs/{run_id}').get_json()['status'] == 'running':
                break
            time.sleep(.05)
        self.assertEqual(self.client.get(f'/api/runs/{run_id}').get_json()['status'], 'running')
        time.sleep(.2)
        peer = subprocess.run([IPERF, '-c', '127.0.0.1', '-p', str(port), '-t', '2', '-R'],
                              capture_output=True, timeout=12, creationflags=CREATE_FLAGS)
        self.assertEqual(peer.returncode, 0, peer.stderr)
        result = self.finish(run_id)
        self.assertEqual(result['status'], 'completed', result.get('error'))
        self.assertEqual(result['effective_settings']['duration'], 2)
        self.assertEqual(result['effective_settings']['mode'], 'download')
        self.assertGreater(result['metrics']['download_mbps'], 0)

    def test_http_validation_and_page_assets(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        with self.client.get('/static/vendor/adminlte/adminlte.min.css') as asset:
            self.assertEqual(asset.status_code, 200)
        if os.name == 'nt':
            self.assertEqual(self.client.post('/api/runs', json={'target': '127.0.0.1', 'protocol': 'udp', 'mode': 'bidir'}).status_code, 400)
        self.assertEqual(self.client.get('/api/runs/not-a-run').status_code, 404)
        self.assertEqual(self.client.post('/api/runs', json={'target': 'host;id'}).status_code, 400)
        self.assertEqual(self.client.post('/api/runs', data='{}').status_code, 415)
        self.assertEqual(self.client.post('/api/runs', json={'target': 'host'}, headers={'Origin': 'https://other.example'}).status_code, 403)


if __name__ == '__main__': unittest.main()
