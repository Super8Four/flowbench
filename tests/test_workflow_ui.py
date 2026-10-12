"""Semantic UI boundaries that keep optional traffic and reports explicit."""
from html.parser import HTMLParser
import tempfile
import unittest

from app import create_app


class Controls(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.items = []
        self.feed(content)

    def handle_starttag(self, tag, attributes):
        self.items.append((tag, dict(attributes)))

    def control(self, identifier):
        return next(attributes for _, attributes in self.items if attributes.get('id') == identifier)


class WorkflowUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING': True, 'RUNS_DIRECTORY': self.directory.name,
                               'IPERF_PATH': 'missing-iperf'})
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions['iperf_runs'].shutdown()
        self.directory.cleanup()

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return Controls(response.get_data(as_text=True))

    def test_pages_have_unique_controls_and_resolved_labels(self):
        for path in ('/', '/projects', '/history', '/comparison', '/configuration', '/servers'):
            with self.subTest(path=path):
                page = self.page(path)
                identifiers = [attributes['id'] for _, attributes in page.items if 'id' in attributes]
                self.assertEqual(len(identifiers), len(set(identifiers)))
                for tag, attributes in page.items:
                    if tag == 'label' and 'for' in attributes:
                        self.assertIn(attributes['for'], identifiers)

    def test_probes_and_flent_require_explicit_selection(self):
        page = self.page('/')
        for identifier in ('latency', 'endpoint_health', 'mtu', 'traceroute', 'flent-confirm'):
            self.assertNotIn('checked', page.control(identifier))
        self.assertIn('disabled', page.control('flent-start'))
        self.assertEqual(page.control('flent-start')['type'], 'button')
        self.assertEqual(page.control('matrix-start')['type'], 'button')

    def test_reports_start_redacted_without_raw_or_unpreviewed_download(self):
        for path in ('/', '/comparison'):
            with self.subTest(path=path):
                page = self.page(path)
                self.assertIn('checked', page.control('report-redact'))
                self.assertNotIn('checked', page.control('report-raw'))
                self.assertIn('disabled', page.control('report-download'))
                self.assertIn('disabled', page.control('report-preview-button'))

    def test_comparison_can_add_rows_and_rate_labels_are_dynamic(self):
        page = self.page('/comparison')
        self.assertEqual(page.control('add-comparison')['type'], 'button')
        identifiers = {attributes.get('id') for _, attributes in page.items}
        self.assertIn('comparison-selections', identifiers)
        self.assertIn('baseline', identifiers)
        self.assertNotIn('candidate', identifiers)
        self.assertNotIn('third', identifiers)
        self.assertIn('comparison-unit', identifiers)
        self.assertIn('comparison-visible-count', identifiers)
        test_page = self.page('/')
        for identifier in ('throughput-unit', 'upload-unit', 'download-unit', 'result-upload-unit',
                           'result-download-unit', 'live-unit', 'flent-chart-unit'):
            test_page.control(identifier)
        self.assertEqual(test_page.control('saved-color')['type'], 'color')
