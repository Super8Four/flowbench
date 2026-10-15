# FlowBench changelog

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Published prerelease](docs/assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1) [![Native images](docs/assets/badges/platform.svg)](docs/setup-and-testing.md) [![Storage](docs/assets/badges/storage.svg)](docs/sqlite-storage.md)

## Unreleased

- Distinguish iPerf session upload/download with green/yellow local and remote shades. Add explicit source/direction legends and consistent line patterns. Assign separate comparison-series colors beyond the saved eight-color run palette; match PDF/Word colors and retain saved history colors, elapsed gaps and SI units.

## 0.2.0-rc.1 - 2026-10-15

This prerelease brings the merged measurement, diagnostic and reporting workflows into the release downloads. It retains SQLite schema 1 and the existing persistent data volume. See [release notes](docs/release-0.2.0-rc.1.md) for install, upgrade and verification links.

- Preserve partial endpoint availability through streamed start/end updates and finalization until fresh matching socket evidence reconciles it. Keep unknown-field enrichment, bounded lists and portable formats unchanged; older archived labels are not rewritten.
- Update minimum Flask, python-dotenv and Waitress versions and use ReportLab 5.0.1 after regression and rendered-report review.
- Refresh the README, platform guides and synthetic screenshots. Keep the supplied transparent SVG artwork and required third-party notices.

### Test endpoints

Current main adds optional captured execution hostname, reporter-local/remote socket pairs, typed destinations and initiating web-peer context, with separate current viewing context. These additions are delivered through PR24 and are not included in existing rc.2 downloads. See [endpoint interpretation and privacy](docs/test-endpoints.md).

- Preserve iPerf reporter perspective for reverse/bidirectional traffic, distinct control connections, DNS resolver attempts, ICMP pinned destinations and Flent declared targets without a public-IP service or inferred NAT mapping.
- Keep historical fallback view-only, validate additive JSON/XML metadata before atomic imports, and redact endpoint/context copies in PDF/Word reports even with raw output selected. Existing SQLite schema and persistent volume remain unchanged.
- Remove trailing empty Word body paragraphs that produced a blank final page, preserving the final tables. PR24 records exact-source checks and remaining acceptance limits. Integration creates no release or deployment.

### Checks, route evidence and measurement workflows

These changes are included in 0.2.0-rc.1. Published rc.1 and rc.2 tags and downloads remain immutable.

- Add standalone typed DNS queries and ICMP echo observations, terminal-outcome comparisons and PDF/Word reporting with sensitive-context redaction. Keep unknown sends/RTT and failed captures explicit; ICMP nonresponse is not application or UDP packet loss.
- Publish diagnostic completion after owned work is cleaned up, keeping captured terminal evidence and its completion time stable.
- Add Run again with a new execution identity and source lineage, validated saved configuration and duplicate-request protection. Preserve original results and require current tool/peer confirmations.
- Record STUN and bounded PCAP as planned backlog, TCP port connectivity and Nmap profiles as future possibilities, and Wi-Fi as deferred; none is enabled by the roadmap.
- Capture one bounded route and read-only local clock-quality observation before each outgoing test, including Both/matrix children and Flent. Retain ordered hop evidence, explicit passive-capture limits, UTC verification limits and portable report/import evidence.
- Add offline world and ordered connection views with optional operator-supplied DB-IP City Lite data, approximate public-IP markers, internal/unknown-hop fallbacks and location redaction in reports.
- Keep history and map keyboard focus across polling updates. Expose sidebar/group expanded states and support mobile navigation focus containment, Escape and focus return.
- Keep incoming listener controls disabled until saved settings load, so an early edit cannot be overwritten by the initial status response.
- Integrate transparent outlined branding into the dark sidebar and mobile header, preserving original logo shapes and assets. Refresh current-app documentation and synthetic desktop/mobile screenshots.
- Add independent review regressions for report redaction, Flent IP-family preservation and incomplete RRUL evidence. Keep native Word legends together across page boundaries.
- Reject malformed interval identities atomically during JSON/XML imports. Preserve historical raw evidence while keeping affected rows visible with explicit unavailable-derived-measurement warnings.
- Add Windows/source checks alongside native AMD64/ARM64 CI, pinned actions, report text-retention checks, sanitized summaries, grouped dependency updates and review/release templates. CI packages tested images; publication and deployment remain explicit actions.
- Group runs in named projects with completed baselines, observed change summaries and comparability warnings.
- Capture opt-in native ping, route and MTU evidence, with local CPU/interface counters and explicit missing/blocked states; save diagnostic checkpoints in SQLite.
- Run bounded sequential TCP/UDP, direction and stream-count matrices, retaining cancellation, child failures and restart evidence.
- Add elapsed-time live baseline overlays, endpoint tags and editable timestamped timeline notes with JSON/XML and project interchange.
- Remove the three-run comparison/report cap; retain actual mixed-duration endpoints, save editable colors and use decimal SI rate displays while keeping numeric archive units unchanged.
- Integrate optional actual Linux Flent RRUL through local tools, preserving native result data and stopping owned child processes.
- Generate branded PDF/Word reports for completed tests and comparisons, with default redaction, an evidence preview and stale-preview checks.
- Refresh installation guides and synthetic desktop/mobile screenshots for the merged main features, distinguishing source builds from existing prerelease downloads.
- Keep older 0.1.0-rc.1/rc.2 releases immutable. This release does not deploy the app or complete authentication, retention, scenario thresholds or instance coordination.

## 0.1.0-rc.2 - vector logo update

- Prefer the supplied self-contained SVG logo in the sidebar and favicon, retaining the original PNG fallback, palette and proportions.
- Update README branding and screenshots; remove framework credits, repeated slogans and development-process commentary from product-facing copy.
- Add a linked installation wiki for Windows, Ubuntu native, Docker/Compose and Pi ARM64 caveats, with configuration, maintenance and troubleshooting.
- Include outlined lettering without fonts, scripts, embedded raster or external resources; verify safe SVG content and asset serving.
- Derive CI package names/OCI version labels from the application version and rebuild both native image archives for this prerelease.
- Keep 0.1.0-rc.1 and its downloads unchanged. Database schema and operational/security scope remain unchanged.

## 0.1.0-rc.1 — first prerelease

- Replace the actual JSON/JSONL archive with SQLite transactions in the existing writable data volume. Remove unused PostgreSQL starter dependencies and optional service.
- Import legacy files once without modifying them; retain JSON/XML evidence, additive imports/deduplication, notes/presets and legacy rollback snapshots.
- Add sequential TCP/UDP Both jobs with separate measurements and aggregate outcomes. Default new client tests to bidirectional while preserving saved/legacy choices.
- Add a managed persistent incoming listener that records ordinary clients, runs independently of outgoing tests, saves configuration and identifies overlapping traffic.
- Rename the GitHub repository from `iPerfWeb` to `flowbench`, preserving repository identity/history/settings.
- Introduce FlowBench, "Measure. Compare. Verify.", the supplied logo, flat gunmetal cards, navy/crimson accents, responsive chart labels and accessible validation/focus.
- Expand disposable SQLite, real loopback and native Ubuntu AMD64/ARM64 container checks; package checked images with metadata and checksums.
- Add installation, operation, migration/backup/rollback, troubleshooting, verification and remaining-scope documentation with sanitized synthetic screenshots.

Known limits: no authentication, no automatic retention, no full designed reports/profiles/thresholds or authenticated instance coordination. The Windows engine rejects UDP bidirectional. Target Raspberry Pi deployment and broader acceptance remain unverified. This is a release candidate, not a stable release.
