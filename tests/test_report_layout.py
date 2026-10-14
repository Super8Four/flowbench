"""Regression for a native office heading orphan that text retention missed."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


spec = importlib.util.spec_from_file_location(
    "report_layout_check", Path(__file__).resolve().parents[1] / "scripts" / "check-report-layout.py")
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class ReportLayoutChecks(unittest.TestCase):
    def test_header_footer_only_page_is_rejected_without_counting_final_formfeed(self):
        first = "FlowBench\nNetwork diagnostic report\nFlowBench | Page 1\f"
        blank = "FlowBench\n\nFlowBench | Page 2\n\f"
        last = "FlowBench\nCheck 2\nEvidence table\nFlowBench | Page 3\f"
        self.assertEqual(layout.blank_body_pages(first + blank + last), [2])
        self.assertEqual(layout.blank_body_pages(first + last), [])
        self.assertEqual(layout.blank_body_pages(first + "\f" + last), [2])

    def test_comparison_must_keep_heading_caveats_and_first_body_row_together(self):
        root = ET.fromstring('''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
          <w:p><w:r><w:t>Baseline compared with Test 3</w:t></w:r></w:p>
          <w:p><w:r><w:t>Settings differ.</w:t></w:r></w:p>
          <w:p><w:r><w:t>Different settings: duration, streams</w:t></w:r></w:p>
          <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Metric</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Selected</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>upload rate</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>500 bps</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>upload retransmits</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        </w:body></w:document>''')
        heading = "Baseline compared with Test 3"
        caveats = "Settings differ. Different settings: duration, streams"
        first = "Metric Selected upload rate 500 bps"
        # Later table rows may legitimately continue on another page.
        self.assertEqual(layout.comparison_page_issues(root, heading + caveats + first + "\fupload retransmits 2"), [])
        self.assertEqual(layout.comparison_page_issues(root, heading + "\f" + caveats + first), [heading])
        self.assertEqual(layout.comparison_page_issues(root, heading + caveats + "Metric Selected\fupload rate 500 bps"), [heading])
        # All text exists globally in both failing cases; retention is not enough.


if __name__ == "__main__":
    unittest.main()
