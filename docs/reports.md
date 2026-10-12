# PDF and Word reports

Generate a report from a completed individual test or select up to three completed tests for a comparison. The first selected test is the baseline. Choose individual children from a protocol or matrix batch. Failed, running, cancelled and empty tests are rejected rather than presented as completed evidence.

The preview and both downloads use one versioned report model. PDF and Word contain captured metrics, settings, timestamps, endpoint context when permitted, project labels, timeline notes, measured charts, comparison caveats and available diagnostics. Actual Flent series retain their native names and units in unredacted reports; throughput and RTT are plotted separately. Missing values stay missing. Packet counts describe traffic volume rather than a universal improvement. Settings differences prevent claims that the measured change establishes a network improvement or regression.

Redaction is enabled by default. It removes endpoint addresses and hostnames, labels, project text, tags, notes and captured free text, while retaining measured numbers, statuses and timestamps. Raw evidence is a separate opt-in and is always removed when redaction remains enabled. Unredacted reports can contain sensitive network and user context; review the preview before sharing. Downloads are generated locally with no external upload or font service.

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
