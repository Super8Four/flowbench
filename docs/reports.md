# PDF and Word reports

Generate a report from completed individual tests, with no fixed selection-count cap. The first selected test is the baseline. Choose individual children from a protocol or matrix batch. Failed, running, cancelled and empty tests are rejected rather than presented as completed evidence. Resource budgets are 32 MiB of normalized report data, 500000 measured points, 32 MiB of generated chart images, 500 PDF pages and 32 MiB of Word document content. If exceeded, split the selection into smaller reports.

The preview and both downloads use one versioned report model. PDF and Word contain captured metrics, settings, timestamps, endpoint context when permitted, project labels, timeline notes, measured charts, comparison caveats and available diagnostics. Rate displays use decimal SI bps, kbps, Mbps, Gbps or Tbps, without changing stored native bps or derived Mbps. Saved per-run colors are reused; colors can repeat, so labels identify each trace. Chart panels and legends paginate for larger selections with common axes. Actual Flent throughput and RTT use separate plots. Missing values stay missing. Packet counts describe traffic volume rather than a universal improvement. Settings differences prevent claims that the measured change establishes a network improvement or regression.

Runs with different durations retain their actual endpoints. The elapsed common window is disclosed separately from whole-run summary metrics. A duration-weighted common-window mean is available only where explicit, contiguous intervals exactly cover the window; a partial boundary interval or missing span is reported as unavailable. Summary averages are not recomputed or relabelled as common-window measurements.

Redaction is enabled by default. It removes endpoint addresses and hostnames, labels, project text, tags, notes and captured free text, while retaining measured numbers, statuses and timestamps. Custom imported direction names become generic identifiers and stream labels are removed, including in common-window summaries. This changes report presentation only; saved measurements and JSON/XML archive exports retain the original evidence. Raw evidence is a separate opt-in and is always removed when redaction remains enabled. Unredacted reports can contain sensitive network and user context; review the preview before sharing. Downloads are generated locally with no external upload or font service.

Reports use the saved SQLite snapshot and do not modify measurements or annotations. JSON and XML exports remain the complete portable archive format; PDF and DOCX are readable reports and are not archive import formats. Original diagnostic samples and native results remain in the saved test. Raw report fields are capped at 20000 characters each and explicitly mark truncation.

PDF embeds the offline Bitstream Vera fonts distributed with ReportLab and attaches their original license as `bitstream-vera-LICENSE.txt` inside each PDF. Characters outside the embedded font's glyph set are replaced with a question mark, and this limitation is printed in the report. DOCX retains Unicode text and uses Arial or the reader's font fallback. Microsoft Word is not needed to generate either file. Rendering a DOCX for visual QA requires a compatible office renderer, such as LibreOffice; this is a development check rather than an app runtime dependency.

The report APIs accept a JSON object:

```json
{"ids":["0123456789abcdef0123456789abcdef"],"redact":true,"include_raw":false}
```

- `POST /api/reports/preview` returns the normalized report model.
- `POST /api/reports/download?format=pdf` returns a PDF attachment.
- `POST /api/reports/download?format=docx` returns a Word attachment.

The preview returns a `fingerprint` of its displayed evidence. Pass that fingerprint with the download options to reject a changed archive snapshot and require a fresh preview. Report generation timestamps are excluded from the fingerprint.

The PDF renderer is [ReportLab 4.4.9](https://pypi.org/project/reportlab/4.4.9/) under its BSD license. Word generation uses [python-docx 1.2.0](https://pypi.org/project/python-docx/1.2.0/) under the MIT license. Their installed distributions preserve the upstream notices, including the bundled font license. Report generation does not provide authentication; use FlowBench on loopback or the intended trusted network.
