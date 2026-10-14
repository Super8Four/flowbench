import json
import os
import subprocess
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.runner import RunManager, CREATE_FLAGS
import test_app as existing


def wait_for_incoming_capture(manager, listener, count, *, idle_timeout=10, total_timeout=40, clock=None, sleep=None):
    """Allow active report persistence, but bound both stalls and total waiting.

    A controlled slow-archive reproduction retained all 176 native events after
    28.531 seconds following client exit. Every pretty-report line commits its
    own durable transaction. Only new capture evidence refreshes the unchanged
    ten-second stall budget; clock-worker updates and polling do not count.
    """
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    began = last_progress_at = clock()
    previous = None
    while True:
        with listener.lock:
            runs = [row for row in manager.list_runs() if row.get('incoming') and row['finished_at']]
            record = listener.record or {}
            progress = (len(runs), record.get('id'), record.get('event_count', 0),
                        len(listener.full_lines), listener.end_seen)
            state = {'listener': listener.status(), 'completed_count': len(runs), 'expected_count': count,
                     'current': {key: record.get(key) for key in ('id', 'status', 'event_count', 'finished_at', 'error')},
                     'protocol': record.get('settings', {}).get('protocol'), 'mode': record.get('settings', {}).get('mode'),
                     'interval_count': len(record.get('intervals', [])), 'summary_keys': sorted(record.get('summary', {})),
                     'end_seen': listener.end_seen, 'buffered_report_lines': len(listener.full_lines),
                     'last_report_line': listener.full_lines[-1][:160] if listener.full_lines else None,
                     'monitor_alive': bool(listener.worker and listener.worker.is_alive()),
                     'clock_worker_alive': bool(listener.time_worker and listener.time_worker.is_alive()),
                     'process_exit_code': listener.process.poll() if listener.process else None,
                     'storage_error': manager.storage_error}
        observed = clock()
        # The overall cap remains effective even if progress never stops.
        if observed - began >= total_timeout:
            reason = 'overall_deadline'
        elif len(runs) >= count:
            return [manager.export(row['id']) for row in runs]
        else:
            if progress != previous:
                previous, last_progress_at = progress, observed
            if observed - last_progress_at < idle_timeout:
                sleep(min(.05, total_timeout - (observed - began), idle_timeout - (observed - last_progress_at)))
                continue
            reason = 'no_capture_progress'
        state.update(reason=reason, elapsed_seconds=round(observed - began, 3),
                     idle_seconds=round(observed - last_progress_at, 3),
                     idle_timeout=idle_timeout, total_timeout=total_timeout)
        raise AssertionError('Incoming evidence did not finish: ' + json.dumps(state, sort_keys=True))


class IncomingCaptureWaitTests(unittest.TestCase):
    def fixture(self, evolve):
        class Clock:
            value = 0.0
            def now(self):
                return self.value
            def sleep(self, seconds):
                self.value += max(seconds, .000001)
        clock = Clock()
        listener = Mock(lock=threading.RLock(), record={'id': 'fixture', 'event_count': 1, 'status': 'running'},
                        full_lines=[], end_seen=True, worker=None, time_worker=None, process=None)
        listener.status.return_value = {'state': 'listening', 'error': None}
        manager = Mock(storage_error=None)
        manager.list_runs.side_effect = lambda: evolve(clock.value, listener)
        manager.export.side_effect = lambda run_id: {'id': run_id, 'status': 'completed', 'native_result': {'end': {}}}
        return manager, listener, clock

    def test_continuing_archive_events_allow_slow_completion_beyond_ten_seconds(self):
        def evolve(seconds, listener):
            listener.record['event_count'] = 1 + int(seconds / 3)
            return [{'id': 'fixture', 'incoming': {'fixture': True}, 'finished_at': 'UTC'}] if seconds >= 12 else []
        manager, listener, clock = self.fixture(evolve)
        rows = wait_for_incoming_capture(manager, listener, 1, clock=clock.now, sleep=clock.sleep)
        self.assertEqual(rows[0]['status'], 'completed')
        self.assertGreaterEqual(clock.value, 12)
        self.assertLess(clock.value, 13)

    def test_stalled_report_fails_without_extending_for_a_live_clock_worker(self):
        manager, listener, clock = self.fixture(lambda *_: [])
        listener.time_worker = Mock()
        listener.time_worker.is_alive.return_value = True
        with self.assertRaises(AssertionError) as raised:
            wait_for_incoming_capture(manager, listener, 1, clock=clock.now, sleep=clock.sleep)
        self.assertIn('"reason": "no_capture_progress"', str(raised.exception))
        self.assertIn('"end_seen": true', str(raised.exception))
        self.assertGreaterEqual(clock.value, 10)
        self.assertLess(clock.value, 11)

    def test_report_buffer_progress_is_bounded_by_overall_deadline(self):
        def evolve(seconds, listener):
            listener.full_lines = ['fixture line'] * int(seconds)
            return []
        manager, listener, clock = self.fixture(evolve)
        with self.assertRaises(AssertionError) as raised:
            wait_for_incoming_capture(manager, listener, 1, total_timeout=20, clock=clock.now, sleep=clock.sleep)
        self.assertIn('"reason": "overall_deadline"', str(raised.exception))
        self.assertGreaterEqual(clock.value, 20)
        self.assertLess(clock.value, 21)


@unittest.skipUnless(RunManager.find_executable(), 'Install iPerf3 for managed listener integration checks.')
class ListenerTests(unittest.TestCase):
    setUp = existing.RealIperfTests.setUp
    tearDown = existing.RealIperfTests.tearDown
    port = existing.RealIperfTests.port
    finish = existing.RealIperfTests.finish

    def start_listener(self):
        self.listener = self.app.extensions['iperf_listener']
        port = self.port()
        response = self.client.post('/api/listener', json={'bind': '127.0.0.1', 'port': port, 'max_duration': 10})
        self.assertEqual(response.status_code, 201, response.get_json())
        time.sleep(.4)
        self.assertTrue(self.listener.running, self.listener.status())
        return port

    def raw_client(self, port, flags=(), duration=1):
        result = subprocess.run([RunManager.find_executable(), '-c', '127.0.0.1', '-p', str(port), '-t', str(duration),
                                 '-J', '-b', '2M', *flags], capture_output=True, text=True, timeout=duration + 10, creationflags=CREATE_FLAGS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def wait_captured(self, count):
        return wait_for_incoming_capture(self.manager, self.listener, count)

    def test_plain_clients_multiple_protocols_directions_and_no_duplicate_full_reports(self):
        port = self.start_listener()
        options = [([], 'tcp', 'upload'), (['-R'], 'tcp', 'download'), (['--bidir'], 'tcp', 'bidir'),
                   (['-u'], 'udp', 'upload'), (['-u', '-R'], 'udp', 'download')]
        if os.name != 'nt':
            options.append((['-u', '--bidir'], 'udp', 'bidir'))
        ids = set()
        for index, (flags, protocol, mode) in enumerate(options, 1):
            native = self.raw_client(port, flags)
            runs = self.wait_captured(index)
            fresh = next(run for run in runs if run['id'] not in ids)
            ids.add(fresh['id'])
            self.assertEqual(fresh['status'], 'completed', fresh['error'])
            self.assertEqual(fresh['settings']['role'], 'server')
            self.assertEqual(fresh['effective_settings']['protocol'], protocol)
            self.assertEqual(fresh['effective_settings']['mode'], mode)
            self.assertEqual(fresh['incoming']['remote_host'], '127.0.0.1')
            self.assertTrue(fresh['incoming']['remote_port'])
            self.assertTrue(fresh['native_result']['start']['accepted_connection'])
            self.assertIn('"start"', fresh['raw_stdout'])
            self.assertTrue(fresh['events'])
            for direction in ['upload', 'download'] if mode == 'bidir' else [mode]:
                self.assertGreater(fresh['metrics'][direction + '_mbps'], 0)
            # A complete JSON after streamed end belongs to the same run, never a second capture.
            self.listener.full_report(fresh['native_result'])
            self.assertEqual(len(self.wait_captured(index)), index)
        self.assertTrue(self.listener.running)
        self.assertIsNone(self.manager.active)
        self.assertIsNone(self.listener.record)

    def test_outbound_overlap_and_busy_incoming_rejection(self):
        port = self.start_listener()
        inbound = subprocess.Popen([RunManager.find_executable(), '-c', '127.0.0.1', '-p', str(port), '-t', '4', '-b', '2M', '-J'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=CREATE_FLAGS)
        self.peers.append(inbound)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.listener.record:
            time.sleep(.05)
        self.assertIsNotNone(self.listener.record)
        incoming_id = self.listener.record['id']
        busy = subprocess.run([RunManager.find_executable(), '-c', '127.0.0.1', '-p', str(port), '-t', '1', '-J', '-b', '2M'],
                              capture_output=True, text=True, timeout=5, creationflags=CREATE_FLAGS)
        self.assertNotEqual(busy.returncode, 0)
        error = busy.stdout.lower() + busy.stderr.lower()
        self.assertTrue('busy' in error or (os.name == 'nt' and 'reset' in error), error)
        outbound_port = self.port()
        peer = subprocess.Popen([RunManager.find_executable(), '-s', '-1', '-B', '127.0.0.1', '-p', str(outbound_port)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
        self.peers.append(peer)
        time.sleep(.3)
        response = self.client.post('/api/runs', json={'target': '127.0.0.1', 'port': outbound_port, 'duration': 1, 'mode': 'bidir'})
        self.assertEqual(response.status_code, 201, response.get_json())
        outgoing_id = response.get_json()['id']
        self.assertNotEqual(outgoing_id, incoming_id)
        self.assertEqual(self.finish(outgoing_id)['status'], 'completed')
        self.assertEqual(inbound.communicate(timeout=10)[1], '')
        incoming = self.wait_captured(1)[0]
        self.assertEqual(incoming['status'], 'completed', incoming['error'])
        self.assertIn(outgoing_id, incoming['concurrency']['overlap_run_ids'])
        outgoing = self.manager.export(outgoing_id)
        self.assertIn(incoming_id, outgoing['concurrency']['overlap_run_ids'])
        self.assertEqual(len([r for r in self.manager.list_runs() if r.get('incoming')]), 1)

    def test_enabled_listener_restarts_without_replaying_history_and_disable_persists(self):
        port = self.start_listener()
        self.raw_client(port)
        before = self.wait_captured(1)[0]
        self.manager.shutdown()
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name, 'IPERF_PATH': RunManager.find_executable()})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']
        self.listener = self.app.extensions['iperf_listener']
        time.sleep(.4)
        self.assertTrue(self.listener.running, self.listener.status())
        self.assertEqual(self.manager.export(before['id']), before)
        self.raw_client(port, ['-R'])
        self.assertEqual(len(self.wait_captured(2)), 2)
        self.assertEqual(self.client.post('/api/listener/stop', json={}).status_code, 200)
        self.assertFalse(self.listener.running)
        self.assertFalse(self.manager.storage.listener_config()['enabled'])
        self.listener.consume_line(json.dumps({'event': 'error', 'data': 'interrupt - server terminated by signal'}))
        self.listener.full_report({'start': {}, 'error': 'interrupt - server terminated by signal'})
        self.assertIsNone(self.listener.error)

    def test_stop_active_incoming_retains_partial_evidence(self):
        port = self.start_listener()
        inbound = subprocess.Popen([RunManager.find_executable(), '-c', '127.0.0.1', '-p', str(port), '-t', '8', '-b', '2M', '-J'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
        self.peers.append(inbound)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not (self.listener.record and self.listener.record['intervals']):
            time.sleep(.05)
        self.assertIsNotNone(self.listener.record)
        self.assertTrue(self.listener.record['intervals'])
        self.client.post('/api/listener/stop', json={})
        records = self.wait_captured(1)
        self.assertEqual(records[0]['status'], 'cancelled')
        self.assertTrue(records[0]['intervals'])
        self.assertTrue(records[0]['events'])
        self.assertFalse(self.listener.running)

    def test_listener_validates_settings_and_shared_port_conflicts(self):
        for data in [{'bind': 'host;whoami'}, {'port': True}, {'port': 65536}, {'max_duration': 0}, {'max_duration': 3601}]:
            self.assertEqual(self.client.post('/api/listener', json=data).status_code, 400)
        self.assertFalse(self.app.extensions['iperf_listener'].running)
        server_port = self.port()
        server = self.manager.start({'role': 'server', 'bind': '127.0.0.1', 'port': server_port})
        self.assertEqual(self.client.post('/api/listener', json={'port': server_port}).status_code, 409)
        self.manager.stop(server['id'])
        self.manager.worker.join(5)
        port = self.start_listener()
        self.assertEqual(self.client.post('/api/listener', json={'port': port}).status_code, 409)
        self.assertEqual(self.client.post('/api/runs', json={'role': 'server', 'port': port}).status_code, 409)
        self.assertEqual(self.client.post('/api/listener/stop', json={}, headers={'Origin': 'https://other.example'}).status_code, 403)

    def test_incoming_json_xml_roundtrip_preserves_original_evidence(self):
        port = self.start_listener()
        self.raw_client(port, ['-u', '-R'])
        original = self.wait_captured(1)[0]
        content = self.client.get('/api/runs/' + original['id'] + '/export?format=xml').data.decode()
        response = self.client.post('/api/runs/import', json={'filename': 'incoming.json', 'content': json.dumps(original)})
        self.assertEqual(response.status_code, 200, response.get_json())
        imported = self.manager.export(response.get_json()['imported'][0])
        for field in ['events', 'incoming', 'native_result', 'test_parameters', 'summary', 'settings', 'intervals']:
            self.assertEqual(imported[field], original[field])
        duplicate = self.client.post('/api/runs/import', json={'filename': 'incoming.xml', 'content': content})
        self.assertEqual(duplicate.get_json()['duplicates'], [imported['id']])

    def test_unexpected_listener_exit_preserves_partial_session_and_explicit_restart(self):
        port = self.start_listener()
        inbound = subprocess.Popen([RunManager.find_executable(), '-c', '127.0.0.1', '-p', str(port), '-t', '8', '-b', '2M', '-J'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
        self.peers.append(inbound)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and not (self.listener.record and self.listener.record['intervals']):
            time.sleep(.05)
        self.assertTrue(self.listener.record['intervals'])
        self.listener.process.kill()
        self.listener.worker.join(5)
        record = self.wait_captured(1)[0]
        self.assertEqual(record['status'], 'interrupted')
        self.assertTrue(record['events'] and record['intervals'])
        self.assertEqual(self.listener.status()['state'], 'failed')
        self.assertTrue(self.listener.config['enabled'])
        self.assertEqual(self.client.post('/api/listener', json={'bind': '127.0.0.1', 'port': port, 'max_duration': 10}).status_code, 201)
        time.sleep(.4)
        self.raw_client(port)
        self.assertEqual(len(self.wait_captured(2)), 2)

    def test_failed_config_write_still_stops_runtime_listener(self):
        self.start_listener()
        with patch.object(self.manager.storage, 'save_listener_config', side_effect=OSError('Synthetic full archive')):
            response = self.client.post('/api/listener/stop', json={})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.listener.running)
        self.listener.consume_line(json.dumps({'event': 'error', 'data': 'interrupt - server terminated by signal'}))
        self.listener.full_report({'start': {}, 'error': 'interrupt - server terminated by signal'})
        self.assertIn('could not be saved', self.listener.error)
        self.assertTrue(self.manager.storage.listener_config()['enabled'])

    def test_bind_collision_is_visible_without_fake_incoming_run(self):
        port = self.port()
        peer = subprocess.Popen([RunManager.find_executable(), '-s', '-B', '127.0.0.1', '-p', str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
        self.peers.append(peer)
        time.sleep(.4)
        self.client.post('/api/listener', json={'bind': '127.0.0.1', 'port': port})
        self.listener = self.app.extensions['iperf_listener']
        self.listener.worker.join(5)
        self.assertEqual(self.listener.status()['state'], 'failed')
        self.assertTrue(self.listener.error)
        self.assertFalse(self.manager.list_runs())

    def test_maintenance_suppresses_saved_listener_without_changing_enabled_state(self):
        self.start_listener()
        self.manager.shutdown()
        with patch.dict(os.environ, {'FLOWBENCH_RESUME_LISTENER': '0'}):
            self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name, 'IPERF_PATH': RunManager.find_executable()})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']
        self.listener = self.app.extensions['iperf_listener']
        self.assertFalse(self.listener.running)
        self.assertTrue(self.listener.config['enabled'])

    def test_capture_write_failure_stops_listener_and_blocks_new_traffic(self):
        port = self.start_listener()
        with patch.object(self.manager.storage, 'append', side_effect=OSError('Synthetic archive write failure')):
            inbound = subprocess.Popen([RunManager.find_executable(), '-c', '127.0.0.1', '-p', str(port), '-t', '2', '-b', '2M', '-J'],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_FLAGS)
            self.peers.append(inbound)
            self.listener.worker.join(8)
        self.assertFalse(self.listener.running)
        self.assertEqual(self.listener.state, 'failed')
        self.assertTrue(self.manager.storage_error)
        record = self.wait_captured(1)[0]
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['event_count'], 0)
        self.assertEqual(record['events'], [])
        self.assertEqual(self.client.get('/api/ready').status_code, 503)
        self.assertEqual(self.client.post('/api/listener', json={'port': port}).status_code, 503)


if __name__ == '__main__':
    unittest.main()
