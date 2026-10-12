# FlowBench project state

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

FlowBench supports TCP/UDP/Both tests, incoming capture, saved history, notes/presets, comparisons and JSON/XML interchange. SQLite stores the archive in the existing persistent data volume. Legacy files import without modification.

0.1.0-rc.2 adds the outlined SVG logo with PNG fallback and simplifies the product copy. The rc.1 tag and downloads remain unchanged. Test results, image metadata and screenshots are in [verification](verification.md) and the [release notes](release-0.1.0-rc.2.md).

Profiles/thresholds, full reports, retention, authentication and authenticated instance coordination remain pending. Keep the API within a trusted access boundary. Target Pi deployment, hardware power-loss recovery and full requirements/WCAG/audit acceptance remain unverified. See [remaining requirements](requirements.md).
