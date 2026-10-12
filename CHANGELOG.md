# FlowBench changelog

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=feature%2Fsqlite-storage)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Afeature%2Fsqlite-storage) [![Prerelease](docs/assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) [![Native images](docs/assets/badges/platform.svg)](docs/setup-and-testing.md) [![Storage](docs/assets/badges/storage.svg)](docs/sqlite-storage.md)

## Unreleased - measurement workflows

- Group runs in named projects with completed baselines, observed change summaries and comparability warnings.
- Capture opt-in native ping, route and MTU evidence, with local CPU/interface counters and explicit missing/blocked states; save diagnostic checkpoints in SQLite.
- Run bounded sequential TCP/UDP, direction and stream-count matrices, retaining cancellation, child failures and restart evidence.
- Add elapsed-time live baseline overlays, endpoint tags and editable timestamped timeline notes with JSON/XML and project interchange.
- Remove the three-run comparison/report cap; retain actual mixed-duration endpoints, save editable colors and use decimal SI rate displays while keeping numeric archive units unchanged.
- Integrate optional actual Linux Flent RRUL through local tools, preserving native result data and stopping owned child processes.
- Generate branded PDF/Word reports for completed tests and comparisons, with default redaction, an evidence preview and stale-preview checks.
- Keep rc.1 and rc.2 immutable. This feature branch does not deploy the app or complete authentication, retention, scenario thresholds or instance coordination.

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
