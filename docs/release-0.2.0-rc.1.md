# FlowBench 0.2.0-rc.1

Network measurements, saved evidence and reports in one portable workspace. This prerelease includes the features merged since 0.1.0-rc.2; older tags and downloads remain unchanged.

## Included

- iPerf3 TCP/UDP/Both tests, incoming capture, projects and baselines, bounded matrices and elapsed-time comparisons.
- Standalone DNS and ICMP observations, fresh Run again attempts, route/local clock evidence and optional offline maps.
- Captured socket endpoints, execution hostname and separate initiating/current web context. Partial evidence stays partial through end-only updates until matching observations reconcile it.
- JSON/XML interchange and redacted PDF/Word reports, plus optional Linux Flent RRUL integration.
- Transparent SVG branding, updated platform guides and synthetic screenshots.

## Install or upgrade

Use the [installation guides](install/README.md). The [release page](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1) supplies native `linux/amd64` and `linux/arm64` image archives. For each architecture, keep its `.tar.gz`, `.image.json` and `.SHA256SUMS` together and verify the checksums before loading. The compatibility image tag remains `iperf-workspace:local`. GitHub also provides source archives for the immutable tag.

Before an upgrade, stop traffic and the app, then back up the complete archive to a new destination. Keep the same Compose project and `iperf_data` volume. SQLite schema 1 remains; legacy JSON/JSONL migration leaves its input files intact. New optional evidence fields do not make every older application compatible. Follow [backup, migration and fresh-copy rollback](sqlite-storage.md); never remove newer evidence to force a downgrade.

## Verification

The release page records the exact source commit, terminal Windows/source and native AMD64/ARM64 CI runs, asset checksums and packaging metadata. [Verification](verification.md) records browser/screenshot provenance and rendered-report coverage. A moving main badge is not evidence for a different source commit.

Source, Windows, actual owned-loopback native endpoints, persistent-volume lifecycle, Flent and PDF/Word layout checks are required before publication. The images run non-root with a read-only root and a persistent writable data volume. The optional Flent test image, private archives and QA databases are not release assets.

## Limits and future work

This is a prerelease, not a production deployment or physical Pi certification. The API has no login. Keep it on localhost or behind authenticated access. Fresh release-source interactive browser checks were unavailable because no browser surface was connected; retained screenshots and earlier checks keep their actual source provenance. Full WCAG/screen-reader acceptance, hardware power-loss recovery, physical Pi performance and actual Microsoft Word verification remain unrun. Word fixtures use LibreOffice conversion.

The Windows engine does not support UDP bidirectional. Flent requires separate Linux tools and an authorized Netserver peer. Geographic data is separately licensed and is not bundled. Partial-status corrections do not rewrite labels already saved by older code.

Profiles/thresholds, retention, authentication and instance coordination remain pending. Optional Npcap, Nmap profiles, live packet capture and PCAP/PCAPNG analysis are [planned future scope](roadmap.md), not included features. No capture driver is required for the existing iPerf/ping/route workflows.

Required [software, font and dataset notices](../THIRD_PARTY_NOTICES.md) remain included. Review integration-specific licensing before adding optional external tools.
