"""Navigation semantics on every page and an offline controller behavior fixture."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from app import create_app


class NavigationMarkup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))


class NavigationUiTests(unittest.TestCase):
    def test_every_page_resolves_disclosure_controls_and_loads_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            try:
                for path in ('/', '/history', '/comparison', '/projects', '/server', '/configuration', '/servers'):
                    with self.subTest(path=path):
                        response = app.test_client().get(path)
                        self.assertEqual(response.status_code, 200)
                        elements = NavigationMarkup(response.get_data(as_text=True)).elements
                        ids = {attributes['id']: (tag, attributes) for tag, attributes in elements if 'id' in attributes}
                        toggle = ids['navigation-toggle'][1]
                        self.assertEqual(toggle['aria-controls'], 'workspace-sidebar')
                        self.assertEqual(toggle['aria-expanded'], 'true')
                        self.assertEqual(ids['navigation-close'][0], 'button')
                        groups = [(tag, attributes) for tag, attributes in elements if 'data-navigation-group' in attributes]
                        self.assertEqual(len(groups), 2)
                        for tag, attributes in groups:
                            self.assertEqual(tag, 'button')
                            self.assertEqual(attributes['type'], 'button')
                            self.assertEqual(attributes['aria-expanded'], 'true')
                            self.assertEqual(ids[attributes['aria-controls']][0], 'ul')
                            self.assertEqual(ids[attributes['aria-controls']][1]['role'], 'list')
                        scripts = [attributes['src'] for tag, attributes in elements if tag == 'script' and 'src' in attributes]
                        self.assertLess(scripts.index('/static/vendor/adminlte/adminlte.min.js'), scripts.index('/static/navigation.js'))
            finally:
                app.extensions['iperf_runs'].shutdown()

    @unittest.skipUnless(shutil.which('node'), 'Node is unavailable; native CI runs this offline controller fixture.')
    def test_keyboard_state_tracks_vendor_drawer_and_group_changes(self):
        script = Path(__file__).with_name('navigation-behavior.mjs')
        module = Path(__file__).resolve().parent.parent / 'app' / 'static' / 'navigation.js'
        result = subprocess.run([shutil.which('node'), str(script), str(module)], text=True,
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Navigation behavior passed:', result.stdout)
