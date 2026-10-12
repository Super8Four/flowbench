"""Offline, read-only syntax and local-document checks for first-party source.

Run: python scripts/check-source.py [--node /path/to/node] [--output ci-artifacts/source.json]
No application imports, external links, live databases or network probes are used.
"""

import argparse
import ast
from html.parser import HTMLParser
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent


class HtmlLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.links.append(value)
            elif key == "srcset" and value:
                self.links.extend(item.strip().split()[0] for item in value.split(",") if item.strip())


def source_files(pattern, directories):
    return sorted({path for directory in directories for path in (ROOT / directory).rglob(pattern)
                   if "__pycache__" not in path.parts and "vendor" not in path.parts})


def document_links(path):
    text = path.read_text(encoding="utf-8-sig")
    text = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$", "", text)
    links = re.findall(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)", text)
    links.extend(re.findall(r"(?m)^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", text))
    parser = HtmlLinks()
    parser.feed(text)
    return [value.strip("<>") for value in links] + parser.links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--output")
    args = parser.parse_args()
    failures, counts = [], {"python_files": 0, "javascript_files": 0, "documents": 0, "local_links": 0, "pinned_actions": 0}
    python_files = source_files("*.py", ("app", "scripts", "tests")) + [ROOT / "wsgi.py"]
    for path in python_files:
        counts["python_files"] += 1
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path.relative_to(ROOT)))
        except (SyntaxError, UnicodeError) as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    js_files = source_files("*.js", ("app/static",)) + source_files("*.mjs", ("tests",))
    if not args.node:
        failures.append("Node.js is required for first-party JavaScript syntax checks.")
    else:
        for path in js_files:
            counts["javascript_files"] += 1
            result = subprocess.run([args.node, "--check", str(path)], capture_output=True, text=True, timeout=30)
            if result.returncode:
                failures.append(f"{path.relative_to(ROOT)}: JavaScript syntax check failed: {result.stderr.strip()}")
    documents = sorted(ROOT.glob("*.md")) + source_files("*.md", ("docs", ".github"))
    for path in documents:
        counts["documents"] += 1
        for link in document_links(path):
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue  # External URLs and anchors require separate review.
            target = (path.parent / unquote(parsed.path)).resolve()
            counts["local_links"] += 1
            if not target.is_relative_to(ROOT) or not target.exists():
                failures.append(f"{path.relative_to(ROOT)}: missing or outside-repository local link: {link}")
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.search(r"\buses:\s*([^\s#]+)", line)
            if not match or match[1].startswith(("./", "docker://")):
                continue
            counts["pinned_actions"] += 1
            if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", match[1]):
                failures.append(f"{path.relative_to(ROOT)}: action is not pinned to a full commit SHA: {match[1]}")
    report = {"schema_version": 1, "python": platform.python_version(), "checks": counts,
              "passed": not failures, "failures": failures,
              "limits": "Syntax, first-party local link existence and action pin format only; no external URL, browser, runtime or acceptance verification."}
    if args.output:
        output = (ROOT / args.output).resolve()
        if not output.is_relative_to(ROOT):
            raise ValueError("Write the CI summary inside this checkout.")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
