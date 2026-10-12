"""Check that LibreOffice PDF conversion retains generated Word report text.

This host-side CI check uses standard Python and Poppler pdftotext. It does
not generate reports, invoke network probes or replace visual page inspection.
"""
import argparse
from collections import Counter
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from zipfile import ZipFile


WORD_TEXT = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def normalize(text):
    return re.sub(r"\s+", "", text)


def check(document, converted, tool):
    with ZipFile(document) as package:
        root = ET.fromstring(package.read("word/document.xml"))
    expected = Counter(normalize(node.text or "") for node in root.iter(WORD_TEXT)
                       if len(normalize(node.text or "")) >= 6)
    # Raw content order preserves each table cell's wrapped text. Layout mode
    # can interleave adjacent cells and manufacture a false missing-text match.
    result = subprocess.run([tool, "-raw", "-enc", "UTF-8", str(converted), "-"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=30, check=True)
    actual = normalize(result.stdout)
    missing = [(text, count, actual.count(text)) for text, count in expected.items() if actual.count(text) < count]
    print(f"{document.name}: {len(expected)} distinct text items checked; {len(missing)} missing or under-counted.")
    for text, count, found in missing:
        print(f"  Expected {count}, found {found}: {text}")
    return not missing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx-dir", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    args = parser.parse_args()
    tool = shutil.which("pdftotext")
    if not tool:
        parser.error("Poppler pdftotext is required for this host-side layout check.")
    documents = sorted(args.docx_dir.glob("*.docx"))
    if not documents:
        parser.error("No generated Word report fixtures were found.")
    passed = True
    for document in documents:
        converted = args.pdf_dir / (document.stem + ".pdf")
        if not converted.is_file():
            print("Missing converted PDF: " + str(converted))
            passed = False
            continue
        try:
            passed = check(document, converted, tool) and passed
        except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError) as error:
            print(f"Could not check {document.name}: {error}")
            passed = False
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
