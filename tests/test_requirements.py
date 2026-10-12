import copy
import json
import subprocess
import io
import unittest
from unittest.mock import Mock, patch

from app.route import capture_route, parse_hops, route_command
from app.runner import settings_from
import test_imports as fixtures
saved_run = fixtures.saved_run


class WorkspaceTests(unittest.TestCase):
    setUp = fixtures.ImportTests.setUp
    tearDown = fixtures.ImportTests.tearDown
    upload = fixtures.ImportTests.upload
    def test_focused_pages_and_tree_navigation(self):
        for path, section in [('/', 'test-form'), ('/server', 'listener-form'), ('/configuration', 'listener-form'), ('/history', 'history-body'),
                              ('/comparison', 'comparison-chart'), ('/servers', 'preset-body')]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            text = response.get_data(as_text=True)
            self.assertIn('data-lte-toggle="treeview"', text)
            self.assertIn('id="' + section + '"', text)
            for other in {'test-form', 'listener-form', 'history-body', 'comparison-chart', 'preset-body'} - {section}:
                self.assertNotIn('id="' + other + '"', text)

    def test_three_run_comparison_roundtrip_and_validation(self):
        originals = []
        for i in range(3):
            row = saved_run(); row['id'] = str(i) * 32
            row['settings']['label'] = 'Fixture ' + str(i)
            row['summary']['sum_received']['bits_per_second'] = (i + 1) * 100e6
            row['intervals'][0]['sum']['bits_per_second'] = (i + 1) * 100e6
            row['route'] = {'status': 'captured', 'hops': parse_hops('1  1 ms  127.0.0.1'), 'raw_output': 'original route'}
            originals.append(row)
        ids = self.upload('/api/runs/import', {'kind': 'multi-comparison', 'runs': originals}).get_json()['imported']
        query = '/api/comparison?' + '&'.join('id=' + id for id in ids)
        report = self.client.get(query).get_json()
        self.assertEqual(len(report['runs']), 3)
        self.assertEqual(len(report['comparisons']), 2)
        self.assertEqual([run['series'][0]['mbps'] for run in report['runs']], [100,200,300])
        self.assertNotIn('events', report['runs'][0])
        for xml in (False, True):
            exported = self.client.get(query + '&format=' + ('xml' if xml else 'json'))
            result = self.client.post('/api/runs/import', json={'filename': 'comparison.xml' if xml else 'comparison.json', 'content': exported.get_data(as_text=True)})
            self.assertEqual(result.status_code, 200, result.get_json())
            recovered = result.get_json()['imported'] + result.get_json()['duplicates']
            self.assertEqual(len(recovered), 3)
            self.assertEqual(self.manager.export(recovered[0])['route']['raw_output'], 'original route')
        for bad in ([ids[0]], [ids[0], ids[0]], ids + [ids[0]]):
            self.assertEqual(self.client.get('/api/comparison?' + '&'.join('id=' + id for id in bad)).status_code, 400)
        self.assertEqual(self.client.get(query + '&format=csv').status_code, 400)

    def test_import_rejects_malformed_routes(self):
        row = saved_run(); row['route'] = {'status': 'captured', 'hops': 'invalid'}
        self.assertEqual(self.upload('/api/runs/import', row).status_code, 400)

    def test_post_test_route_failure_keeps_throughput_success(self):
        process = Mock()
        process.stdout = io.StringIO(json.dumps({'end': saved_run()['summary']}) + '\n')
        process.stderr = io.StringIO('')
        process.wait.return_value = 0
        self.manager.capabilities['available'] = True
        with patch('app.runner.subprocess.Popen', return_value=process), patch('app.runner.capture_route', return_value={'status':'unavailable', 'hops':[], 'note':'Not installed'}) as capture:
            record = self.manager.start({'target':'127.0.0.1','mode':'upload','traceroute':True})
            self.manager.worker.join(5)
        self.assertFalse(self.manager.worker.is_alive())
        result = self.manager.export(record['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['metrics']['upload_mbps'], 100)
        self.assertEqual(result['route']['status'], 'unavailable')
        capture.assert_called_once()

    def test_stop_during_route_capture_cancels_and_keeps_measurements(self):
        process = Mock()
        process.stdout = io.StringIO(json.dumps({'end': saved_run()['summary']}) + '\n')
        process.stderr = io.StringIO(''); process.wait.return_value = 0; process.poll.return_value = None
        self.manager.capabilities['available'] = True
        def route(settings, begin, cancelled):
            begin(['mock-route', '127.0.0.1'])
            self.manager.stop(self.manager.active)
            self.assertTrue(cancelled())
            return {'status':'cancelled','hops':[]}
        with patch('app.runner.subprocess.Popen', return_value=process), patch('app.runner.capture_route', side_effect=route):
            record = self.manager.start({'target':'127.0.0.1','mode':'upload','traceroute':True})
            self.manager.worker.join(5)
        self.assertFalse(self.manager.worker.is_alive())
        result = self.manager.export(record['id'])
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(result['metrics']['upload_mbps'],100)
        process.terminate.assert_called_once()


class RouteTests(unittest.TestCase):
    def test_windows_linux_hops_preserve_unknown_replies(self):
        hops = parse_hops('  1    <1 ms    2 ms    3 ms  192.168.1.1\n  2     *        *        *     Request timed out.\n  3    10 ms  2001:db8::1')
        self.assertEqual(hops[0]['rtt_ms'], [1,2,3])
        self.assertFalse(hops[1]['responded'])
        self.assertEqual(hops[2]['address'], '2001:db8::1')
        self.assertEqual(parse_hops(' 1?: [LOCALHOST] pmtu 1500\n 1:  192.168.1.1  0.233ms')[0]['rtt_ms'], [0.233])

    def test_command_is_bounded_without_shell_or_privilege(self):
        with patch('app.route.os.name', 'posix'), patch('app.route.shutil.which', return_value='/usr/bin/tracepath'):
            self.assertEqual(route_command('2001:db8::1','6'), ['/usr/bin/tracepath','-n','-m','16','-6','2001:db8::1'])
        with self.assertRaises(ValueError): settings_from({'target': 'host', 'traceroute': 'yes'})

    def test_timeout_and_cancellation_preserve_output(self):
        process = Mock(returncode=-1)
        process.communicate.side_effect = [subprocess.TimeoutExpired('tracepath',20), ('1:  127.0.0.1  0.1ms','')]
        with patch('app.route.route_command', return_value=['tracepath','127.0.0.1']):
            result = capture_route(settings_from({'target':'127.0.0.1'}), lambda _:process, lambda:False)
            self.assertEqual(result['status'], 'timeout'); process.kill.assert_called_once()
            self.assertEqual(result['hops'][0]['address'],'127.0.0.1')
            self.assertEqual(capture_route({'target':'host'}, lambda _:None, lambda:True)['status'],'cancelled')

    def test_missing_tool_is_reported_without_launch(self):
        launch = Mock()
        with patch('app.route.route_command', return_value=None):
            self.assertEqual(capture_route({'target':'host'}, launch, lambda:False)['status'],'unavailable')
        launch.assert_not_called()
