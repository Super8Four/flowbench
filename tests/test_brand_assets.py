from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from app import create_app
import app as application

BRAND = Path(application.__file__).resolve().parent / 'static' / 'brand'


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.items = []

    def handle_starttag(self, tag, attrs):
        self.items.append((tag, dict(attrs)))


class BrandAssetsTests(unittest.TestCase):
    def test_logo_is_self_contained_vector_without_active_content(self):
        content = (BRAND / 'flowbench-logo.svg').read_bytes()
        self.assertNotIn(b'<!DOCTYPE', content.upper())
        self.assertNotIn(b'<!ENTITY', content.upper())
        root = ET.fromstring(content)
        allowed = {'svg': {'width', 'height', 'viewBox', 'role', 'aria-labelledby'},
                   'title': {'id'}, 'desc': {'id'}, 'path': {'d', 'fill', 'fill-rule'}}
        paths = []
        for node in root.iter():
            self.assertTrue(node.tag.startswith('{http://www.w3.org/2000/svg}'))
            name = node.tag.rsplit('}', 1)[-1]
            self.assertIn(name, allowed)
            self.assertLessEqual(set(node.attrib), allowed[name])
            self.assertTrue(all('url(' not in value.lower() for value in node.attrib.values()))
            if name == 'path':
                paths.append(node)
                self.assertRegex(node.attrib['fill'], r'^#[0-9A-Fa-f]{6}$')
                self.assertRegex(node.attrib['d'], r'^[MmLlHhVvCcSsQqTtAaZz0-9.,+\-\seE]+$')
        self.assertTrue(paths)
        self.assertEqual(root.attrib['viewBox'], '0 0 2172 724')
        self.assertEqual(root.attrib['aria-labelledby'], 'title desc')
        self.assertTrue(root.find('{http://www.w3.org/2000/svg}title').text)

    def test_dark_logo_preserves_outlines_without_a_background_plate(self):
        original = ET.parse(BRAND / 'flowbench-logo.svg').getroot()
        dark = ET.parse(BRAND / 'flowbench-logo-dark.svg').getroot()
        ns = '{http://www.w3.org/2000/svg}'
        before, after = original.findall(ns + 'path'), dark.findall(ns + 'path')
        self.assertEqual(len(before), len(after))
        for a, b in zip(before, after):
            self.assertEqual(a.attrib['d'], b.attrib['d'])
            self.assertEqual(a.attrib['fill-rule'], b.attrib['fill-rule'])
            self.assertEqual(b.attrib['fill'], {'#343A40': '#E3EDF5', '#234058': '#8EB4D1'}.get(a.attrib['fill'], a.attrib['fill']))
        self.assertEqual(dark.attrib['viewBox'], '64 152 2048 432')
        self.assertEqual({n.tag for n in dark}, {ns + 'title', ns + 'desc', ns + 'path'})
        self.assertNotIn(b'url(', (BRAND / 'flowbench-logo-dark.svg').read_bytes())

    def test_app_serves_transparent_dark_svg_and_preserves_original_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            try:
                client = app.test_client()
                svg = client.get('/static/brand/flowbench-logo.svg')
                dark = client.get('/static/brand/flowbench-logo-dark.svg')
                png = client.get('/static/brand/flowbench-logo.png')
                self.assertEqual(svg.status_code, 200)
                self.assertEqual(svg.mimetype, 'image/svg+xml')
                self.assertEqual(svg.data, (BRAND / 'flowbench-logo.svg').read_bytes())
                self.assertEqual(dark.status_code, 200)
                self.assertEqual(dark.mimetype, 'image/svg+xml')
                self.assertEqual(dark.data, (BRAND / 'flowbench-logo-dark.svg').read_bytes())
                self.assertEqual(png.mimetype, 'image/png')
                self.assertTrue(png.data.startswith(b'\x89PNG\r\n\x1a\n'))
                for route in ['/', '/history', '/comparison', '/projects', '/server', '/servers', '/configuration']:
                    parser = Tags()
                    parser.feed(client.get(route).data.decode())
                    self.assertIn(('img', {'src': '/static/brand/flowbench-logo-dark.svg', 'alt': 'FlowBench', 'width': '2048', 'height': '432'}), parser.items)
                    self.assertIn(('a', {'href': '/', 'class': 'header-brand-link', 'aria-label': 'FlowBench home'}), parser.items)
                    icons = [attrs for tag, attrs in parser.items if tag == 'link' and attrs.get('rel') == 'icon']
                    self.assertEqual({item['type'] for item in icons}, {'image/png', 'image/svg+xml'})
            finally:
                svg.close()
                dark.close()
                png.close()
                app.extensions['iperf_runs'].shutdown()
