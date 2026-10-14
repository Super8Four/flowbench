"""Preflight evidence stays semantic, optional-diagnostics independent and read-only."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import app as application
from app import create_app


class Structure(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.stack = []
        self.elements = {}
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if attributes.get('id'):
            self.elements[attributes['id']] = (tag, attributes, tuple(self.stack))
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append((tag, attributes.get('id')))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break


class PreflightUiTests(unittest.TestCase):
    def test_native_hop_list_and_clock_fields_are_always_available_on_test_page(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            try:
                response = app.test_client().get('/')
                self.assertEqual(response.status_code, 200)
                content = response.get_data(as_text=True)
                page = Structure(content)
                tag, attributes, _ = page.elements['preflight-evidence']
                self.assertEqual(tag, 'section')
                self.assertNotIn('d-none', attributes.get('class', ''))
                self.assertNotIn('traceroute', page.elements)
                self.assertEqual(page.elements['route-hops'][0], 'ol')
                self.assertEqual(page.elements['preflight-status'][1]['aria-live'], 'polite')
                self.assertEqual(page.elements['preflight-status'][1]['aria-atomic'], 'true')
                for identifier in ['clock-query-status', 'clock-observed-at', 'clock-daemon-state',
                                   'clock-source', 'clock-offset', 'clock-uncertainty', 'clock-uncertainty-kind']:
                    ancestors = page.elements[identifier][2]
                    self.assertIn(('section', 'preflight-evidence'), ancestors)
                    self.assertNotIn(('section', 'diagnostics-card'), ancestors)
                for identifier in ['route-raw-details', 'clock-raw-details', 'route-table-details', 'route-hop-observations']:
                    self.assertEqual(page.elements[identifier][0], 'details')
                    self.assertNotIn('open', page.elements[identifier][1])
                for mode in ['world', 'topology']:
                    control = page.elements[f'route-view-{mode}'][1]
                    self.assertEqual(control['type'], 'button')
                    self.assertEqual(control['aria-controls'], f'route-{mode}-panel')
                    self.assertEqual(page.elements[f'route-{mode}-map'][0], 'svg')
                    self.assertEqual(page.elements[f'route-{mode}-map'][1]['role'], 'group')
                self.assertEqual(page.elements['route-map-observation'][0], 'select')
                self.assertEqual(page.elements['route-location-rows'][0], 'tbody')
                self.assertIn('not device GPS', content)
                self.assertIn('private, missing and ambiguous hops break', content)
                self.assertIn('https://db-ip.com', content)
                self.assertIn('CC BY 4.0', content)
                for path in ['/projects', '/history', '/comparison']:
                    self.assertIn('IP Geolocation by DB-IP', app.test_client().get(path).get_data(as_text=True))
                self.assertIn('Each outgoing test attempts one native route preflight before traffic.', content)
                self.assertIn('Read-only local clock', content.replace('read-only', 'Read-only'))
            finally:
                app.extensions['iperf_runs'].shutdown()

    @unittest.skipUnless(shutil.which('node'), 'Node is absent; the source CI job runs this isolated JavaScript controller fixture.')
    def test_saved_preflight_evidence_controller_behavior(self):
        script = Path(__file__).resolve().with_name('preflight-ui-behavior.mjs')
        module = Path(application.__file__).resolve().parent / 'static' / 'workflows.js'
        result = subprocess.run([shutil.which('node'), str(script), str(module)],
                                text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Preflight UI behavior passed:', result.stdout)
