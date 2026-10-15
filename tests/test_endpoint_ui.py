"""Endpoint panel markup and actual DOM/API controller behavior, without traffic."""
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
        self.elements = {}
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get('id'):
            self.elements[attributes['id']] = (tag, attributes)


class EndpointUiTests(unittest.TestCase):
    def test_endpoint_panel_precedes_progress_and_exposes_semantic_copy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            try:
                content = app.test_client().get('/').get_data(as_text=True)
                controls = Controls(content).elements
                self.assertLess(content.index('id="test-endpoints"'), content.index('id="result-cards"'))
                self.assertLess(content.index('id="test-endpoints"'), content.index('id="live-metrics"'))
                self.assertEqual(controls['test-endpoints'][1]['aria-labelledby'], 'endpoint-heading')
                for name in ['local', 'peer', 'web']:
                    tag, attributes = controls['endpoint-copy-' + name]
                    self.assertEqual(tag, 'button')
                    self.assertEqual(attributes['type'], 'button')
                    self.assertIn('disabled', attributes)
                for name in ['connections', 'web-context']:
                    tag, attributes = controls['endpoint-' + name]
                    self.assertEqual(tag, 'details')
                    self.assertNotIn('open', attributes)
                self.assertIn('readonly', controls['endpoint-copy-value'][1])
                self.assertIn('hidden', controls['endpoint-copy-fallback'][1])
                self.assertEqual(controls['endpoint-copy-feedback'][1]['role'], 'status')
                self.assertEqual(controls['endpoint-copy-feedback'][1]['aria-live'], 'polite')
                self.assertIn('These ports are not data-stream ports', content)
                self.assertIn('Saved initiating web peer', content)
                self.assertIn('Current viewing web peer', content)
                self.assertNotIn('id="test-endpoints"', app.test_client().get('/configuration').get_data(as_text=True))
            finally:
                app.extensions['iperf_runs'].shutdown()

    @unittest.skipUnless(shutil.which('node'), 'Node absent; source CI runs the isolated JavaScript fixture.')
    def test_endpoint_dom_api_and_clipboard_behavior(self):
        script = Path(__file__).resolve().with_name('endpoints-ui-behavior.mjs')
        static = Path(application.__file__).resolve().parent / 'static'
        result = subprocess.run([shutil.which('node'), str(script), str(static)], text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Endpoint UI behavior passed:', result.stdout)
