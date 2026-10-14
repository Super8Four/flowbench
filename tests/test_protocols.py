import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.exports import xml_export
from app.imports import import_runs
from app.runner import RunManager, TERMINAL, CREATE_FLAGS
import test_app as existing_tests
from test_imports import saved_run


def process_for(protocol, code=0):
    params = {'protocol': protocol.upper(), 'bidir': 1, 'duration': 1, 'num_streams': 1}
    summary = {'sum_received': {'bits_per_second': 2e6}, 'sum_received_bidir_reverse': {'bits_per_second': 3e6}}
    if protocol == 'udp':
        for row in summary.values():
            row.update(jitter_ms=.25, lost_percent=0, lost_packets=0, packets=100)
    interval = {'sum': {'end': 1, 'bits_per_second': 2e6}, 'sum_bidir_reverse': {'end': 1, 'bits_per_second': 3e6}}
    events = [{'event': 'start', 'data': {'test_start': params}}]
    if code == 0:
        events += [{'event': 'interval', 'data': interval},
                   {'event': 'server_output_json', 'data': {'start': {'test_start': params}, 'end': summary, 'intervals': [interval]}},
                   {'event': 'end', 'data': summary}]
    else:
        events += [{'event': 'error', 'data': 'Fixture connection failure'}]
    process = Mock(stdout=io.StringIO(''.join(json.dumps(event) + '\n' for event in events)), stderr=io.StringIO())
    process.wait.return_value = code
    process.poll.return_value = code
    return process


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name, 'IPERF_PATH': 'missing-iperf'})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']
        self.manager.capabilities.update(available=True, bidir=True, udp_bidir=True, json_stream=True)

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def run_job(self, codes=(0, 0), **settings):
        processes = [process_for(protocol, code) for protocol, code in zip(['tcp', 'udp'], codes)]
        with patch.object(self.manager, 'preflight', return_value=True), patch('app.runner.subprocess.Popen', side_effect=processes) as launch:
            response = self.client.post('/api/runs', json={'target': '127.0.0.1', 'protocol': 'both', 'duration': 1, 'bandwidth': '2M', **settings})
            self.assertEqual(response.status_code, 201, response.get_json())
            self.manager.worker.join(10)
        self.assertFalse(self.manager.worker.is_alive())
        return self.manager.export(response.get_json()['id']), launch

    def test_both_sequential_bidir_identity_metrics_history_and_restart(self):
        job, launch = self.run_job(label='Two protocols')
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['settings']['mode'], 'bidir')
        self.assertEqual([row['settings']['protocol'] for row in job['runs']], ['tcp', 'udp'])
        for index, run in enumerate(job['runs'], 1):
            self.assertEqual(run['protocol_job']['id'], job['id'])
            self.assertEqual(run['protocol_job']['order'], index)
            self.assertEqual(run['metrics']['upload_mbps'], 2)
            self.assertEqual(run['metrics']['download_mbps'], 3)
            self.assertEqual({p['direction'] for p in run['series']}, {'upload', 'download'})
            self.assertTrue(run['peer']['available'])
            self.assertTrue(run['events'])
        self.assertLessEqual(job['runs'][0]['finished_at'], job['runs'][1]['created_at'])
        commands = [call.args[0] for call in launch.call_args_list]
        self.assertNotIn('-u', commands[0])
        self.assertIn('-u', commands[1])
        self.assertIn('--bidir', commands[0])
        self.assertIn('--bidir', commands[1])
        self.assertEqual(commands[1][commands[1].index('-b') + 1], '2M')
        self.assertEqual(len(self.manager.list_runs()), 3)
        view = self.client.get('/api/runs/' + job['id']).get_json()
        self.assertEqual(view['metrics'], {})
        self.assertEqual(view['series'], [])
        self.assertEqual([row['protocol'] for row in view['protocol_runs']], ['tcp', 'udp'])
        self.assertIsNone(self.manager.active_id)
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, 'missing-iperf')
        self.assertEqual(self.manager.export(job['id']), job)

    def test_partial_and_failed_jobs_retain_separate_evidence(self):
        for codes, status in [((0, 1), 'partial'), ((1, 0), 'partial'), ((1, 1), 'failed')]:
            job, launch = self.run_job(codes)
            self.assertEqual(job['status'], status)
            self.assertEqual(launch.call_count, 2)
            for run, code in zip(job['runs'], codes):
                self.assertEqual(run['status'], 'completed' if code == 0 else 'failed')
                if code:
                    self.assertEqual(run['metrics'], {})
                    self.assertTrue(run['error'])

    def test_stop_between_protocols_skips_udp_and_prevents_overlap(self):
        original = self.manager.run
        def stop_after_first(grouped=False):
            self.assertEqual(self.client.post('/api/runs', json={'target': '127.0.0.1'}).status_code, 409)
            original(grouped)
            self.assertIsNotNone(self.manager.active_id)
            self.manager.stop(self.manager.active_id)
        with patch.object(self.manager, 'run', side_effect=stop_after_first):
            job, launch = self.run_job()
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual([row['status'] for row in job['runs']], ['completed', 'skipped'])
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(job['runs'][1]['events'], [])

    def test_stop_during_first_protocol_terminates_and_skips_second(self):
        process = process_for('tcp')
        process.poll.return_value = None
        def stop_wait(timeout=None):
            self.manager.stop(self.manager.active_id)
            return -1
        process.wait.side_effect = stop_wait
        with patch.object(self.manager, 'preflight', return_value=True), patch('app.runner.subprocess.Popen', return_value=process) as launch:
            record = self.manager.start({'target': '127.0.0.1', 'protocol': 'both', 'duration': 1, 'bandwidth': '2M'})
            self.manager.worker.join(10)
        self.assertFalse(self.manager.worker.is_alive())
        result = self.manager.export(record['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual([r['status'] for r in result['runs']], ['cancelled', 'skipped'])
        self.assertEqual(launch.call_count, 1)
        process.terminate.assert_called_once()

    def test_stop_during_udp_retains_completed_tcp_and_cancelled_udp(self):
        udp = process_for('udp')
        udp.poll.return_value = None
        def stop_wait(timeout=None):
            self.manager.stop(self.manager.active_id)
            return -1
        udp.wait.side_effect = stop_wait
        with patch.object(self.manager, 'preflight', return_value=True), patch('app.runner.subprocess.Popen', side_effect=[process_for('tcp'), udp]):
            record = self.manager.start({'target': '127.0.0.1', 'protocol': 'both', 'duration': 1, 'bandwidth': '2M'})
            self.manager.worker.join(10)
        self.assertFalse(self.manager.worker.is_alive())
        result = self.manager.export(record['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual([r['status'] for r in result['runs']], ['completed', 'cancelled'])
        self.assertEqual(result['runs'][0]['metrics']['upload_mbps'], 2)
        udp.terminate.assert_called_once()

    def test_default_new_start_and_presets_preserve_explicit_and_old_import_modes(self):
        for supplied, expected in [({}, 'bidir'), ({'mode': 'upload'}, 'upload'), ({'mode': 'download'}, 'download')]:
            with patch.object(self.manager, 'preflight', return_value=True), patch('app.runner.subprocess.Popen', return_value=process_for('tcp')):
                record = self.manager.start({'target': '127.0.0.1', **supplied})
                self.manager.worker.join(5)
            self.assertEqual(record['settings']['mode'], expected)
        for supplied, expected in [({}, 'bidir'), ({'mode': 'upload'}, 'upload')]:
            response = self.client.post('/api/presets', json={'name': 'New ' + expected, 'settings': {'target': 'example.test', **supplied}})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.get_json()['settings']['mode'], expected)
        old = saved_run()
        old['settings'].pop('mode')
        imported = import_runs(self.manager, old, 'old.json')['imported'][0]
        self.assertEqual(self.manager.read(imported)['settings']['mode'], 'upload')
        preset = self.client.post('/api/presets', json={'name': 'Both preset', 'settings': {'target': 'example.test', 'protocol': 'both', 'mode': 'download'}})
        self.assertEqual(preset.status_code, 201)
        self.assertEqual(preset.get_json()['settings']['protocol'], 'both')
        for field in ['mode', 'protocol']:
            self.assertEqual(self.app.extensions['iperf_presets'].read()['presets'][-1]['settings'][field], preset.get_json()['settings'][field])

    def test_job_json_xml_import_atomicity_identity_deduplication_and_partial_snapshots(self):
        job, _ = self.run_job((0, 1))
        for xml in [False, True]:
            body = xml_export(job).decode() if xml else json.dumps(job)
            response = self.client.post('/api/runs/import', json={'content': body, 'filename': 'job.xml' if xml else 'job.json'})
            self.assertEqual(response.status_code, 200, response.get_json())
            result = response.get_json()
            if not xml:
                imported = self.manager.export(result['imported'][0])
                self.assertEqual(imported['status'], 'partial')
                for source, recovered in zip(job['runs'], imported['runs']):
                    self.assertEqual(recovered['protocol_job']['id'], imported['id'])
                    for key in ['settings', 'summary', 'events', 'created_at', 'finished_at', 'native_result']:
                        self.assertEqual(recovered[key], source[key])
            else:
                self.assertEqual(result['duplicates'][0], imported['id'])
        count = len(self.manager.list_runs())
        for change in ['protocol', 'references', 'status']:
            broken = copy.deepcopy(job)
            if change == 'protocol': broken['runs'][1]['settings']['protocol'] = 'tcp'
            if change == 'references': broken['children'][0]['id'] = 'a' * 32
            if change == 'status': broken['status'] = 'completed'
            response = self.client.post('/api/runs/import', json={'content': json.dumps(broken)})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.manager.list_runs()), count)
        snapshot = copy.deepcopy(job)
        snapshot.update(status='running', finished_at=None)
        snapshot['runs'][1].update(status='queued', finished_at=None)
        result = import_runs(self.manager, snapshot, 'snapshot.json')
        self.assertEqual(self.manager.read(result['imported'][0])['status'], 'interrupted')
        self.assertEqual(self.manager.read(result['imported'][2])['status'], 'interrupted')

    def test_job_restart_never_resumes_queued_protocol(self):
        with patch.object(self.manager, 'run_job'):
            record = self.manager.start({'target': '127.0.0.1', 'protocol': 'both', 'bandwidth': '2M'})
            self.manager.worker.join(5)
        self.manager.active = None  # Simulate startup after an abrupt process exit.
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, 'missing-iperf')
        result = self.manager.export(record['id'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual([r['status'] for r in result['runs']], ['interrupted', 'interrupted'])
        self.assertIsNone(self.manager.worker)

    def test_legacy_rollback_files_preserve_job_and_child_identities(self):
        job, _ = self.run_job()
        snapshot = Path(self.temp.name) / 'rollback'
        self.manager.storage.export_legacy(snapshot)
        restored = RunManager(snapshot, 'missing-iperf')
        try:
            self.assertEqual(restored.export(job['id']), job)
        finally:
            restored.shutdown()

    def test_unsupported_bidir_both_and_server_both_fail_before_any_write_or_launch(self):
        self.manager.capabilities['udp_bidir'] = False
        with patch.object(self.manager, 'preflight', return_value=True), patch('app.runner.subprocess.Popen') as launch:
            for settings in [{'protocol': 'both'}, {'protocol': 'udp'}, {'role': 'server', 'protocol': 'both'}]:
                response = self.client.post('/api/runs', json={'target': '127.0.0.1', **settings})
                self.assertEqual(response.status_code, 400)
            launch.assert_not_called()
        self.assertEqual(self.manager.list_runs(), [])

    def test_initial_job_write_and_group_import_failures_are_atomic(self):
        original = self.manager.storage.save
        writes = []
        def fail_third(record, connection=None):
            writes.append(record['id'])
            if len(writes) == 3:
                raise OSError('Simulated archive failure')
            return original(record, connection)
        with patch.object(self.manager.storage, 'save', side_effect=fail_third), patch.object(self.manager, 'preflight', return_value=True), patch('app.runner.subprocess.Popen') as launch:
            response = self.client.post('/api/runs', json={'target': '127.0.0.1', 'protocol': 'both', 'bandwidth': '2M'})
            self.assertEqual(response.status_code, 503)
            launch.assert_not_called()
        self.assertEqual(self.manager.list_runs(), [])
        self.assertIsNone(self.manager.active)
        job, _ = self.run_job()
        count = len(self.manager.list_runs())
        writes.clear()
        with patch.object(self.manager.storage, 'save', side_effect=fail_third):
            response = self.client.post('/api/runs/import', json={'content': json.dumps(job)})
            self.assertEqual(response.status_code, 503)
        self.assertEqual(len(self.manager.list_runs()), count)
        self.assertEqual(len(import_runs(self.manager, job, 'retry.json')['imported']), 3)

    def test_failure_before_udp_launch_preserves_completed_tcp(self):
        original = self.manager.save
        def deny_udp_start(record):
            if record['settings']['protocol'] == 'udp' and record['status'] == 'starting':
                raise OSError('Simulated unavailable storage before UDP')
            return original(record)
        with patch.object(self.manager, 'save', side_effect=deny_udp_start):
            job, launch = self.run_job()
        self.assertEqual(job['status'], 'partial')
        self.assertEqual([run['status'] for run in job['runs']], ['completed', 'skipped'])
        self.assertEqual(launch.call_count, 1)

    def test_ui_options_bidir_default_and_protocol_job_results(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('value="both">Both (TCP then UDP)', page)
        self.assertIn('value="bidir" selected', page)
        self.assertIn('id="protocol-job-card"', page)
        with self.client.get('/static/app.js') as asset:
            script = asset.get_data(as_text=True)
        self.assertIn("['udp', 'both']", script)
        self.assertNotIn("$('mode').value = 'upload'", script)


@unittest.skipUnless(RunManager.find_executable(), 'Install iPerf3 for protocol loopback tests.')
class RealProtocolTests(unittest.TestCase):
    setUp = existing_tests.RealIperfTests.setUp
    tearDown = existing_tests.RealIperfTests.tearDown
    port = existing_tests.RealIperfTests.port
    finish = existing_tests.RealIperfTests.finish

    def peer(self):
        port = self.port()
        peer = subprocess.Popen([RunManager.find_executable(), '-s', '-B', '127.0.0.1', '-p', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=CREATE_FLAGS)
        self.peers.append(peer)
        time.sleep(.4)
        return port

    def check_mode(self, protocol, **settings):
        response = self.client.post('/api/runs', json={'target': '127.0.0.1', 'port': self.peer(),
                                                     'protocol': protocol, 'duration': 1, 'bandwidth': '2M', **settings})
        if protocol in {'udp', 'both'} and os.name == 'nt' and settings.get('mode', 'bidir') == 'bidir':
            self.assertEqual(response.status_code, 400)
            return
        self.assertEqual(response.status_code, 201, response.get_json())
        result = self.finish(response.get_json()['id'])
        self.assertEqual(result['status'], 'completed', result.get('error'))
        records = self.manager.export(result['id'])['runs'] if protocol == 'both' else [self.manager.export(result['id'])]
        for run in records:
            for direction in ['upload', 'download'] if settings.get('mode', 'bidir') == 'bidir' else ['upload']:
                self.assertGreater(run['metrics'][direction + '_mbps'], 0)
                self.assertTrue(any(p['direction'] == direction for p in run['series']))
        return records

    def test_tcp_default_bidir_and_udp_bidir_capability(self):
        self.check_mode('tcp')
        self.check_mode('udp')

    def test_both_sequential_upload_and_native_bidir(self):
        records = self.check_mode('both', mode='upload')
        self.assertEqual([r['settings']['protocol'] for r in records], ['tcp', 'udp'])
        self.check_mode('both')


if __name__ == '__main__':
    unittest.main()
