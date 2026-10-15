"""Control API checks use only a disposable archive and an owned DNS peer."""

import copy
import json
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import dns.message
import dns.rcode
import dns.rrset

from app import create_app
from app.imports import parse_document


class CheckControlAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.requests = []
        self.requests_lock = threading.Lock()
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                wire, connection = self.request
                query = dns.message.from_wire(wire)
                question = query.question[0]
                with owner.requests_lock:
                    owner.requests.append((str(question.name), question.rdtype))
                response = dns.message.make_response(query)
                if str(question.name).startswith('missing.'):
                    response.set_rcode(dns.rcode.NXDOMAIN)
                else:
                    response.answer.append(dns.rrset.from_text(str(question.name), 30, 'IN', 'A', '203.0.113.7'))
                connection.sendto(response.to_wire(), self.client_address)

        self.server = socketserver.ThreadingUDPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.server_thread.start()
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.temp.name,
                               'IPERF_PATH': str(Path(self.temp.name) / 'missing-iperf')})
        self.client = self.app.test_client()
        self.manager = self.app.extensions['iperf_runs']

    def tearDown(self):
        self.manager.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(2)
        self.temp.cleanup()

    def config(self, **changes):
        return {'check_type': 'dns', 'target': 'fixture.example.test', 'record_type': 'A',
                'resolver_mode': 'custom', 'resolver_address': '127.0.0.1',
                'resolver_port': self.server.server_address[1], 'transport': 'udp',
                'count': 1, 'timeout_ms': 2000, 'label': 'Synthetic DNS', **changes}

    def finish(self, run_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            record = self.manager.read(run_id)
            if record['finished_at']:
                return record
            time.sleep(.03)
        self.fail('Owned DNS check did not finish: ' + repr(self.manager.read(run_id)))

    def start(self, **changes):
        response = self.client.post('/api/checks', json=self.config(**changes))
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['id']

    def test_dns_rerun_retry_export_and_comparison_without_iperf(self):
        self.assertFalse(self.manager.capabilities['available'])
        request_id = uuid4().hex
        source_id = self.start(request_id=request_id)
        duplicate = self.client.post('/api/checks', json=self.config(request_id=request_id))
        self.assertEqual(duplicate.status_code, 201, duplicate.get_json())
        self.assertEqual(duplicate.get_json()['id'], source_id)
        original = self.finish(source_id)
        self.assertEqual(original['status'], 'completed', original.get('error'))
        self.assertEqual(original['result']['attempts'][0]['answers'][0]['rdata'], '203.0.113.7')
        original_export = copy.deepcopy(self.manager.export(source_id))
        plan_response = self.client.get(f'/api/runs/{source_id}/rerun-plan')
        self.assertEqual(plan_response.status_code, 200, plan_response.get_json())
        self.assertEqual(plan_response.headers['Cache-Control'], 'no-store')
        intent = {'request_id': uuid4().hex, 'source_fingerprint': plan_response.get_json()['source_fingerprint']}
        replay = self.client.post(f'/api/runs/{source_id}/rerun', json=intent)
        self.assertEqual(replay.status_code, 201, replay.get_json())
        self.assertFalse(replay.get_json()['replayed'])
        second_id = replay.get_json()['run']['id']
        self.assertNotEqual(source_id, second_id)
        retried = self.client.post(f'/api/runs/{source_id}/rerun', json=intent)
        self.assertEqual(retried.status_code, 200, retried.get_json())
        self.assertTrue(retried.get_json()['replayed'])
        self.assertEqual(retried.get_json()['run']['id'], second_id)
        second = self.finish(second_id)
        self.assertEqual(second['status'], 'completed', second.get('error'))
        self.assertEqual(second['lineage']['source_run_id'], source_id)
        self.assertEqual(self.manager.export(source_id), original_export)
        self.assertEqual(len(self.requests), 2, self.requests)

        detail = self.client.get(f'/api/runs/{second_id}').get_json()
        self.assertEqual(detail['metrics'], {})
        self.assertEqual(detail['series'], [])
        self.assertFalse(detail['peer']['available'])
        history = self.client.get('/api/runs').get_json()['runs']
        self.assertEqual({row['id'] for row in history}, {source_id, second_id})
        self.assertTrue(all(row['check_type'] == 'dns' and row['result']['summary'] for row in history))
        self.assertTrue(all('attempts' not in row['result'] for row in history), 'Polling history must omit raw attempt bodies.')
        query = [('id', source_id), ('id', second_id)]
        comparison = self.client.get('/api/check-comparison', query_string=query)
        self.assertEqual(comparison.status_code, 200, comparison.get_json())
        self.assertEqual(comparison.get_json()['kind'], 'diagnostic-comparison')
        self.assertNotIn('common_window', comparison.get_json())
        self.assertEqual(self.client.get('/api/comparison', query_string=query).status_code, 400)
        for format_name in ('json', 'xml'):
            response = self.client.get('/api/check-comparison', query_string=query + [('format', format_name)])
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
            document = parse_document(response.data)
            self.assertEqual([row['id'] for row in document['runs']], [source_id, second_id])
            self.assertEqual(document['runs'][1]['lineage']['source_run_id'], source_id)

        budget = sum(len(json.dumps(record, ensure_ascii=False).encode('utf-8')) for record in (original, second)) + 1024
        oversized_export = {**original_export, 'raw_stdout': 'x' * (budget + 1)}
        with patch('app.comparisons.MAX_COMPARISON_BYTES', budget), patch.object(self.manager, 'export', return_value=oversized_export) as export:
            limited = self.client.get('/api/check-comparison', query_string=query + [('format', 'json')])
        self.assertEqual(limited.status_code, 413)
        self.assertEqual(export.call_count, 1, 'Stop before accumulating another oversized export.')

    def test_invalid_and_conflicting_control_requests_do_not_send_dns(self):
        for changes in ({'count': True}, {'resolver_address': 'not-an-ip'}, {'request_id': '../bad'}, {'record_type': 'AXFR'}):
            response = self.client.post('/api/checks', json=self.config(**changes))
            self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.client.post('/api/checks', json=self.config(), headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.requests, [])
        token = uuid4().hex
        source_id = self.start(request_id=token)
        self.finish(source_id)
        conflicting = self.client.post('/api/checks', json=self.config(request_id=token, target='other.example.test'))
        self.assertEqual(conflicting.status_code, 409, conflicting.get_json())
        self.assertEqual(len(self.requests), 1)
        plan = self.client.get(f'/api/runs/{source_id}/rerun-plan').get_json()
        rerun = self.client.post(f'/api/runs/{source_id}/rerun', json={'request_id': uuid4().hex, 'source_fingerprint': '0' * 64})
        self.assertEqual(rerun.status_code, 409, rerun.get_json())
        self.assertEqual(len(self.manager.list_runs()), 1)
        self.assertEqual(plan['kind'], 'diagnostic-check')

    def test_terminal_adapter_checkpoint_stays_active_until_cleanup_then_is_stable(self):
        from app.checks import CheckCapture, check_run_status, validate_check_record
        from app.imports import prepare_run
        original_run = CheckCapture.run
        for cancel in (False, True):
            with self.subTest(cancel_during_cleanup=cancel):
                reached, release = threading.Event(), threading.Event()
                def gated(capture, checkpoint):
                    def publish(result):
                        checkpoint(result)
                        if check_run_status(result) in {'completed', 'failed', 'partial', 'cancelled'}:
                            reached.set()
                            if not release.wait(15):
                                raise AssertionError('Terminal checkpoint cleanup barrier was not released')
                    return original_run(capture, publish)
                token = uuid4().hex
                with patch.object(CheckCapture, 'run', gated):
                    run_id = self.start(request_id=token)
                    try:
                        self.assertTrue(reached.wait(10), self.manager.read(run_id))
                        active = self.manager.export(run_id)
                        validate_check_record(active)
                        self.assertEqual(active['status'], 'running')
                        self.assertIsNone(active['finished_at'])
                        self.assertEqual(active['result']['status'], 'running')
                        self.assertIsNone(active['result']['finished_at'])
                        self.assertEqual(len(active['result']['attempts']), 1)
                        self.assertEqual(self.manager.storage.read(run_id)['result'], active['result'])
                        imported, _ = prepare_run(active, 'active-check.json')
                        self.assertEqual(imported['status'], 'interrupted')
                        self.assertEqual(imported['result']['attempts'], active['result']['attempts'])
                        retry = self.client.post('/api/checks', json=self.config(request_id=token))
                        self.assertEqual(retry.get_json()['id'], run_id)
                        self.assertEqual(self.client.post('/api/checks', json=self.config()).status_code, 409)
                        self.assertEqual(self.client.get(f'/api/runs/{run_id}/rerun-plan').status_code, 409)
                        if cancel:
                            self.manager.stop(run_id)
                    finally:
                        release.set()
                        self.manager.worker.join(10)
                    self.assertFalse(self.manager.worker.is_alive())
                terminal = self.manager.export(run_id)
                validate_check_record(terminal)
                self.assertEqual(terminal['status'], 'cancelled' if cancel else 'completed')
                self.assertIsNotNone(terminal['finished_at'])
                self.assertIsNone(self.manager.active_id)
                # Repeated reads and a later actual capture must not revise the
                # source's terminal timestamps, observations or native output.
                next_id = self.start()
                self.finish(next_id)
                self.manager.worker.join(10)
                self.assertEqual(self.manager.export(run_id), terminal)

    def test_failed_terminal_commit_keeps_durable_active_evidence_and_retry_identity(self):
        from app.checks import validate_check_record
        from app.runner import RunManager
        token = uuid4().hex
        save = self.manager.save
        def reject_terminal(record):
            if record.get('kind') == 'diagnostic-check' and record.get('finished_at') is not None:
                raise OSError('Fixture final transaction failed')
            save(record)
        with patch.object(self.manager, 'save', side_effect=reject_terminal):
            run_id = self.start(request_id=token)
            self.manager.worker.join(10)
            self.assertFalse(self.manager.worker.is_alive())
        retained = self.manager.export(run_id)
        validate_check_record(retained)
        self.assertEqual(retained['status'], 'running')
        self.assertIsNone(retained['finished_at'])
        self.assertEqual(len(retained['result']['attempts']), 1)
        self.assertEqual(self.manager.storage.read(run_id)['result'], retained['result'])
        self.assertEqual(self.manager.active_id, run_id)
        self.assertIn('final transaction failed', self.manager.storage_error)
        self.assertEqual(self.client.post('/api/checks', json=self.config()).status_code, 503)
        requests = len(self.requests)
        self.manager.shutdown()
        self.manager = RunManager(self.temp.name, 'missing-iperf')
        recovered = self.manager.read(run_id)
        validate_check_record(recovered)
        self.assertEqual(recovered['status'], 'interrupted')
        self.assertEqual(recovered['result']['attempts'], retained['result']['attempts'])
        self.assertEqual(self.manager.start_check(self.config(request_id=token))['id'], run_id)
        self.assertEqual(len(self.requests), requests)

    def test_negative_dns_outcome_remains_comparable_without_performance_ranking(self):
        missing = self.start(target='missing.example.test')
        record = self.finish(missing)
        self.assertEqual(record['status'], 'completed', record.get('error'))
        self.assertEqual(record['result']['attempts'][0]['status'], 'nxdomain')
        answer = self.start()
        self.finish(answer)
        response = self.client.get('/api/check-comparison', query_string=[('id', missing), ('id', answer)])
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn('improved', json.dumps(response.get_json()))
        capabilities = self.client.get('/api/checks')
        self.assertEqual(capabilities.status_code, 200)
        self.assertIn('dns', capabilities.get_json())


if __name__ == '__main__':
    unittest.main()
