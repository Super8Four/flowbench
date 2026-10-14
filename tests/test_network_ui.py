"""Typed checks, explicit reruns and timezone display use real UI controllers."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import app as application
from app import create_app


class Controls(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.controls = {}
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get('id'):
            self.controls[attributes['id']] = (tag, attributes)


class NetworkUiTests(unittest.TestCase):
    def test_bounded_check_fields_rerun_review_and_explicit_time_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            try:
                client = app.test_client()
                response = client.get('/')
                self.assertEqual(response.status_code, 200)
                content = response.get_data(as_text=True)
                controls = Controls(content).controls
                self.assertEqual(controls['measurement-type'][0], 'select')
                self.assertIn('disabled', controls['run-button'][1])
                for kind in ['dns', 'loss']:
                    tag, attributes = controls[kind + '-fields']
                    self.assertEqual(tag, 'fieldset')
                    self.assertIn('hidden', attributes)
                    self.assertIn('disabled', attributes)
                    self.assertNotIn('value', controls[kind + '-target'][1])
                for identifier, maximum in [('dns-count', '5'), ('dns-timeout', '3000'), ('loss-count', '60')]:
                    self.assertEqual(controls[identifier][1]['max'], maximum)
                for identifier in ['run-again-netserver', 'run-again-incoming']:
                    self.assertNotIn('checked', controls[identifier][1])
                for identifier in ['run-again-review', 'run-again-retry', 'start-retry']:
                    self.assertIn('hidden', controls[identifier][1])
                self.assertIn('do not run a route probe', content)
                self.assertIn('does not enable the persistent listener', content)
                for path in ['/', '/configuration', '/comparison', '/history', '/projects', '/servers']:
                    page = Controls(client.get(path).get_data(as_text=True)).controls
                    self.assertEqual(page['display-timezone'][0], 'select')
            finally:
                app.extensions['iperf_runs'].shutdown()

    @unittest.skipUnless(shutil.which('node'), 'Node absent; source CI runs the isolated JavaScript fixture.')
    def test_typed_drafts_results_replays_and_dst_controller_behavior(self):
        script = Path(__file__).resolve().with_name('network-checks-ui-behavior.mjs')
        static = Path(application.__file__).resolve().parent / 'static'
        result = subprocess.run([shutil.which('node'), str(script), str(static)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Network checks UI behavior passed:', result.stdout)
