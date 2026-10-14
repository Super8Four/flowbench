# FlowBench project state

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

FlowBench supports TCP/UDP/Both tests, incoming capture, saved history, notes/presets, comparisons and JSON/XML interchange. SQLite stores the archive in the existing persistent data volume. Legacy files import without modification.

0.1.0-rc.2 adds the outlined SVG logo with PNG fallback and simplifies the product copy. The rc.1 tag and downloads remain unchanged. Test results, image metadata and screenshots are in [verification](verification.md) and the [release notes](release-0.1.0-rc.2.md).

Current main (`0.2.0-dev`) includes projects/baselines, bounded sequential matrices, native ping/route/MTU and local health evidence, live comparisons, tags/timeline notes, optional actual Flent RRUL and PDF/Word report generation. Transparent SVG branding integrates directly with the dark sidebar and mobile header. See [workflows](measurement-workflows.md), [Flent prerequisites](flent.md), [reports](reports.md) and current synthetic [screenshots](verification.md). This does not change rc.1 or rc.2 downloads.

The `feature/network-checks-reruns` branch adds [standalone DNS/ICMP checks and Run again](network-checks.md), with typed evidence, same-type comparisons and PDF/Word output. The [diagnostic roadmap](roadmap.md) keeps STUN, packet capture, TCP port checks and Nmap outside this implementation; Wi-Fi is deferred. Existing published downloads are unchanged.

Profiles/thresholds, retention, authentication and authenticated instance coordination remain pending. Keep the API within a trusted access boundary. Target Pi deployment, hardware power-loss recovery and full requirements/WCAG/audit acceptance remain unverified. See [remaining requirements](requirements.md).
