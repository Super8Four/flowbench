# PDF and Word reports

Generate a throughput report from completed individual tests, with no fixed selection-count cap. The first selected test is the baseline. Choose individual children from a protocol or matrix batch. Failed, running, cancelled and empty throughput tests are rejected rather than presented as completed evidence. Resource budgets are 32 MiB of normalized report data, 500000 measured points, 32 MiB of generated chart images, 500 PDF pages and 32 MiB of Word document content. If exceeded, split the selection into smaller reports.

Current main also reports [standalone DNS and ICMP observations](network-checks.md). Terminal failed, partial, cancelled and interrupted checks retain their actual status and missing evidence; active checks are rejected. Select one check type separately from throughput tests. These reports contain typed settings, attempts, outcomes and clock context, with no fabricated throughput series. Default redaction removes DNS names and answer data, resolver/destination addresses, free-form provenance/errors and raw output while preserving safe counts, statuses, response codes, flags, TTL and nullable RTT. Diagnostic comparison changes are absolute values in their stated units, not percentage improvement rankings.

For one record, open its saved detail view. For several, select the baseline and other records in **Compare tests**. In the report controls, keep redaction enabled unless the recipient needs sensitive context, then generate a preview. Review the metrics, limitations and included evidence before downloading PDF or Word. Changing selections or report options requires a fresh preview; changed saved evidence can also invalidate the preview fingerprint. Use JSON/XML export when the recipient needs an importable archive.

Completed Flent records are revalidated against their native RRUL source before reporting. All eight expected TCP load flows and four RTT series require actual finite samples, and derived series must match the source. Historical rows that incorrectly claimed completion, missing sources and partial captures are rejected without rewriting or deleting the archived evidence.

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

Native CI renders the synthetic Word fixtures with LibreOffice, then runs `scripts/check-report-layout.py` using Poppler `pdftotext`. It checks that captured Word text remains present in the converted PDF, including table labels and saved colors across page boundaries. It also checks that each comparison heading, caveats, table header and first metric share a page, and rejects pages containing only the report header and footer. Word comparisons and individual test or check sections start through their heading's page-break property; later table rows may continue with repeated headers. These checks detect omitted rows, orphaned comparison headings and blank body pages; they supplement visual inspection of every rendered page rather than establishing layout quality by themselves.

The PDF renderer is [ReportLab 4.4.9](https://pypi.org/project/reportlab/4.4.9/) under its BSD license. Word generation uses [python-docx 1.2.0](https://pypi.org/project/python-docx/1.2.0/) under the MIT license. Their installed distributions preserve the upstream notices, including the bundled font license. Report generation does not provide authentication; use FlowBench on loopback or the intended trusted network.

## Endpoint/context privacy

Current main extends report snapshots with captured endpoint and initiating-context evidence. PDF/Word regressions and rendered-page review are recorded in [verification](verification.md). Published rc.2 downloads do not include this addition.

On that branch, default redaction removes execution hostnames, local/remote/control/destination addresses, ports, IPv6 scopes, socket IDs and initiating web-peer fields, including nested or arbitrary future copies. Selecting raw evidence does not bypass redaction. Only safe availability/count/family/protocol categories remain. Full JSON/XML exports remain sensitive unredacted evidence.

Unredacted output labels the capturing process/container namespace, each reporter's local/remote perspective, declared versus numeric destinations and proxy/NAT uncertainty. Current viewing context is not substituted into a saved run. Endpoint/context changes are included in preview fingerprints, so changed evidence requires a fresh preview. See [endpoint interpretation and sharing](test-endpoints.md).
