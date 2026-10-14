"""Trusted HTTP context and historical endpoint views use disposable archives."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from app import create_app
from app.endpoints import derive_endpoints, initial_endpoints, request_context
from app.exports import xml_export
from app.imports import parse_document
from app.runner import now
from test_annotations import sample_record


def native_start():
    return {"test_start": {"protocol": "TCP", "duration": 1, "num_streams": 1},
            "connected": [{"socket": 7, "local_host": "127.0.0.1", "local_port": 40123,
                           "remote_host": "127.0.0.2", "remote_port": 5201}]}


class EndpointAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.open_app()

    def open_app(self):
        self.app = create_app({"TESTING": True, "RUNS_DIRECTORY": self.temp.name,
                               "IPERF_PATH": str(Path(self.temp.name) / "missing-iperf")})
        self.client = self.app.test_client()
        self.manager = self.app.extensions["iperf_runs"]
        self.listener = self.app.extensions["iperf_listener"]

    def tearDown(self):
        self.manager.shutdown()
        self.temp.cleanup()

    def test_current_context_uses_wsgi_peer_without_forwarded_host_or_dns_lookup(self):
        headers = {"Host": "forged-host.example", "X-Forwarded-For": "198.51.100.44",
                   "X-Forwarded-Port": "12345", "Forwarded": 'for=198.51.100.45:54321;host=forged.example'}
        with patch("app.runtime_hostname", return_value="fixture-runtime"), \
                patch("socket.getaddrinfo", side_effect=AssertionError("No address lookup")), \
                patch("socket.gethostbyaddr", side_effect=AssertionError("No reverse lookup")):
            for peer, family in (("127.0.0.2", 4), ("::1", 6), (None, None)):
                response = self.client.get('/api/context', headers=headers, environ_overrides={"REMOTE_ADDR": peer})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                result = response.get_json()
                self.assertEqual(set(result), {"schema_version", "execution_hostname", "web_request"})
                self.assertEqual(result['schema_version'], 1)
                self.assertEqual(result['execution_hostname'], 'fixture-runtime')
                self.assertEqual(result['web_request']['source'], 'wsgi_remote_addr')
                self.assertEqual(result['web_request']['peer'], {"address": peer, "port": None, "family": family})
                self.assertNotIn('198.51.100.', json.dumps(result))
        self.assertEqual(self.manager.storage.records(), [])

    def test_every_execution_route_passes_context_as_a_separate_trusted_argument(self):
        body = {"target": "127.0.0.1", "initiator_context": {"peer": {"address": "198.51.100.99"}}}
        routes = (("/api/runs", "start"), ("/api/matrices", "start_matrix"),
                  ("/api/checks", "start_check"), ("/api/flent", "start_flent"),
                  ("/api/runs/" + "a" * 32 + "/rerun", "rerun"))
        for route, method in routes:
            with self.subTest(route=route), patch.object(self.manager, method) as admission:
                admission.return_value = {"run": {"id": "b" * 32}, "replayed": False} if method == 'rerun' else {"id": "b" * 32}
                response = self.client.post(route, json=body, headers={"X-Forwarded-For": "198.51.100.1"},
                                            environ_overrides={"REMOTE_ADDR": "127.0.0.3"})
                self.assertEqual(response.status_code, 201, response.get_json())
                context = admission.call_args.kwargs['initiator_context']
                self.assertEqual(context['source'], 'wsgi_remote_addr')
                self.assertEqual(context['peer'], {"address": "127.0.0.3", "port": None, "family": 4})
                self.assertNotEqual(context, body['initiator_context'])

    def finish_without_traffic(self):
        with self.manager.lock:
            self.manager.record.update(status='cancelled', finished_at=now())
            self.manager.save(self.manager.record)
            self.manager.active = None

    def test_request_retry_preserves_context_and_new_rerun_gets_fresh_context(self):
        self.manager.capabilities.update(available=True, bidir=True, udp_bidir=True, json_stream=True)
        body = {"target": "127.0.0.1", "request_id": uuid4().hex,
                "initiator_context": {"peer": {"address": "198.51.100.99"}},
                "endpoints": {"execution_hostname": "forged-json-host"}}
        rejected = self.client.post('/api/runs', json=body, environ_overrides={"REMOTE_ADDR": "127.0.0.2"})
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertEqual(self.manager.storage.records(), [])
        body.pop('initiator_context')
        with patch.object(self.manager, 'run', side_effect=self.finish_without_traffic), \
                patch('socket.gethostname', return_value='capture-container'):
            response = self.client.post('/api/runs', json=body, environ_overrides={"REMOTE_ADDR": "127.0.0.2"})
            self.assertEqual(response.status_code, 201, response.get_json())
            source_id = response.get_json()['id']
            self.manager.worker.join(3)
        self.assertFalse(self.manager.worker.is_alive())
        original = self.manager.export(source_id)
        self.assertEqual(original['initiator_context']['peer']['address'], '127.0.0.2')
        self.assertEqual(original['endpoints']['execution_hostname'], 'capture-container')
        replay = self.client.post('/api/runs', json=body, environ_overrides={"REMOTE_ADDR": "127.0.0.3"})
        self.assertEqual(replay.status_code, 201, replay.get_json())
        self.assertEqual(replay.get_json()['id'], source_id)
        self.assertEqual(replay.get_json()['initiator_context'], original['initiator_context'])
        plan = self.client.get(f'/api/runs/{source_id}/rerun-plan').get_json()
        with patch.object(self.manager, 'run', side_effect=self.finish_without_traffic), \
                patch('socket.gethostname', return_value='rerun-container'):
            rerun = self.client.post(f'/api/runs/{source_id}/rerun',
                                     json={"request_id": uuid4().hex, "source_fingerprint": plan['source_fingerprint']},
                                     environ_overrides={"REMOTE_ADDR": "127.0.0.4"})
            self.assertEqual(rerun.status_code, 201, rerun.get_json())
            fresh_id = rerun.get_json()['run']['id']
            self.manager.worker.join(3)
        self.assertNotEqual(fresh_id, source_id)
        fresh = self.manager.export(fresh_id)
        self.assertEqual(fresh['initiator_context']['peer']['address'], '127.0.0.4')
        self.assertEqual(fresh['endpoints']['execution_hostname'], 'rerun-container')
        self.assertEqual(self.manager.export(source_id), original)
        self.manager.shutdown()
        self.open_app()
        self.assertEqual(self.manager.export(source_id), original)
        self.assertEqual(self.manager.read(fresh_id)['initiator_context'], fresh['initiator_context'])

    def test_detail_view_keeps_stored_context_separate_and_does_not_change_exports(self):
        record = sample_record(native_result={"start": native_start()})
        record['endpoints'] = initial_endpoints(record, 'archived-container')
        record['endpoints'] = derive_endpoints(record)
        record['initiator_context'] = request_context('127.0.0.7')
        self.manager.save(record)
        before = copy.deepcopy(self.manager.storage.read(record['id']))
        with patch('app.runtime_hostname', return_value='current-container'), \
                patch('socket.gethostname', side_effect=AssertionError('No current hostname for stored evidence')), \
                patch('socket.getaddrinfo', side_effect=AssertionError('No lookup')):
            current = self.client.get('/api/context', environ_overrides={"REMOTE_ADDR": "127.0.0.9"}).get_json()
            detail = self.client.get(f"/api/runs/{record['id']}", environ_overrides={"REMOTE_ADDR": "127.0.0.9"}).get_json()
        self.assertEqual(current['execution_hostname'], 'current-container')
        self.assertEqual(current['web_request']['peer']['address'], '127.0.0.9')
        self.assertEqual(detail['endpoint_view']['execution_hostname'], 'archived-container')
        self.assertEqual(detail['endpoint_view']['provenance'], 'captured_archive')
        self.assertEqual(detail['initiator_context']['peer']['address'], '127.0.0.7')
        self.assertEqual(detail['endpoints'], record['endpoints'])
        self.assertNotIn('provenance', detail['endpoints'])
        for format_name in ('json', 'xml'):
            response = self.client.get(f"/api/runs/{record['id']}/export?format={format_name}")
            self.assertEqual(response.status_code, 200)
            exported = parse_document(response.data)
            self.assertEqual(exported['endpoints'], record['endpoints'])
            self.assertEqual(exported['initiator_context'], record['initiator_context'])
            self.assertNotIn('endpoint_view', exported)
        self.assertEqual(self.manager.storage.read(record['id']), before)
        for format_name in ('json', 'xml'):
            content = json.dumps(exported) if format_name == 'json' else xml_export(exported).decode()
            with patch('socket.gethostname', side_effect=AssertionError('No hostname lookup on import')), \
                    patch('socket.getaddrinfo', side_effect=AssertionError('No address lookup on import')):
                uploaded = self.client.post('/api/runs/import', json={"filename": 'capture.' + format_name, "content": content},
                                            environ_overrides={"REMOTE_ADDR": "127.0.0.9"})
                self.assertEqual(uploaded.status_code, 200, uploaded.get_json())
                result = uploaded.get_json()
                imported_id = (result['imported'] or result['duplicates'])[0]
                imported = self.client.get(f'/api/runs/{imported_id}').get_json()
            self.assertEqual(imported['endpoint_view']['provenance'], 'imported_archive')
            self.assertEqual(imported['endpoint_view']['execution_hostname'], 'archived-container')
            self.assertEqual(imported['initiator_context'], record['initiator_context'])
        self.assertEqual(self.manager.storage.read(record['id']), before)

    def test_historical_import_views_never_fill_missing_hostname_or_web_context(self):
        for index, native in enumerate((None, {"start": native_start()})):
            source = sample_record(run_id=('a' if index == 0 else 'b') * 32, native_result=native)
            for format_name in ('json', 'xml'):
                with patch('socket.gethostname', side_effect=AssertionError('No current hostname for history')), \
                        patch('socket.getaddrinfo', side_effect=AssertionError('No lookup for history')):
                    content = json.dumps(source) if format_name == 'json' else xml_export(source).decode()
                    uploaded = self.client.post('/api/runs/import', json={"filename": 'history.' + format_name, "content": content})
                    self.assertEqual(uploaded.status_code, 200, uploaded.get_json())
                    result = uploaded.get_json()
                    run_id = (result['imported'] or result['duplicates'])[0]
                    before = self.manager.storage.read(run_id)
                    detail = self.client.get(f'/api/runs/{run_id}').get_json()
                    self.assertIsNone(detail['endpoint_view']['execution_hostname'])
                    self.assertEqual(detail['endpoint_view']['provenance'], 'not_recorded' if native is None else 'preserved_native')
                    self.assertNotIn('initiator_context', detail)
                    self.assertNotIn('endpoints', before)
                    self.assertEqual(self.manager.storage.read(run_id), before)

    def test_listener_commits_structured_start_without_inferred_web_initiator(self):
        start = native_start()
        start['accepted_connection'] = {"host": "127.0.0.2", "port": 42001}
        self.listener.command = ['fixture-iperf']
        with patch('app.listener.runtime_hostname', return_value='listener-container'), \
                patch.object(self.listener, 'start_time_quality'):
            self.listener.consume_line(json.dumps({"event": "start", "data": start}))
        run_id = self.listener.record['id']
        captured = self.manager.storage.read(run_id)
        self.assertNotIn('initiator_context', captured)
        self.assertEqual(captured['endpoints']['execution_hostname'], 'listener-container')
        self.assertEqual(captured['endpoints']['sockets'][0]['observer'], 'server')
        self.assertEqual(captured['endpoints']['sockets'][0]['local']['port'], 40123)
        self.assertEqual(captured['endpoints']['control_peers'][0]['peer']['port'], 42001)
        self.assertEqual(captured['event_count'], 1)
        self.assertEqual(json.loads(self.manager.read_events(run_id)[0]['message'])['data'], start)
        original_endpoints = copy.deepcopy(captured['endpoints'])
        full = {"end": {"sum_received": {"bits_per_second": 1_000_000}}}
        self.listener.full_report(full)
        completed = self.manager.storage.read(run_id)
        self.assertEqual(completed['native_result'], full)
        self.assertEqual(completed['incoming']['start'], start)
        self.assertEqual(completed['endpoints'], original_endpoints)
        self.assertNotIn('initiator_context', completed)
