from html.parser import HTMLParser
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from app import create_app

BRAND = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'brand'


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

    def test_app_serves_svg_and_preserves_png_picture_and_favicon_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({'TESTING': True, 'RUNS_DIRECTORY': directory, 'IPERF_PATH': 'missing-iperf'})
            try:
                client = app.test_client()
                svg = client.get('/static/brand/flowbench-logo.svg')
                png = client.get('/static/brand/flowbench-logo.png')
                self.assertEqual(svg.status_code, 200)
                self.assertEqual(svg.mimetype, 'image/svg+xml')
                self.assertEqual(svg.data, (BRAND / 'flowbench-logo.svg').read_bytes())
                self.assertEqual(png.mimetype, 'image/png')
                self.assertTrue(png.data.startswith(b'\x89PNG\r\n\x1a\n'))
                for route in ['/', '/history', '/configuration']:
                    parser = Tags()
                    parser.feed(client.get(route).data.decode())
                    self.assertIn(('source', {'type': 'image/svg+xml', 'srcset': '/static/brand/flowbench-logo.svg'}), parser.items)
                    self.assertIn(('img', {'src': '/static/brand/flowbench-logo.png', 'alt': 'FlowBench', 'width': '2172', 'height': '724'}), parser.items)
                    icons = [attrs for tag, attrs in parser.items if tag == 'link' and attrs.get('rel') == 'icon']
                    self.assertEqual({item['type'] for item in icons}, {'image/png', 'image/svg+xml'})
            finally:
                svg.close()
                png.close()
                app.extensions['iperf_runs'].shutdown()
