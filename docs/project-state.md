# FlowBench project state

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

FlowBench 0.2.0-rc.1 includes iPerf3 throughput, standalone DNS/ICMP checks, incoming capture, saved history/import, comparisons, projects/baselines, bounded matrices, notes and JSON/XML interchange. SQLite uses the existing persistent data volume; legacy files import without modification.

The release also includes captured endpoints and web-control context, route/local clock evidence, optional offline geographic views, optional Linux Flent RRUL and redacted PDF/Word reports. The supplied transparent SVG branding is used on the dark sidebar and mobile header. See [release notes](release-0.2.0-rc.1.md), [installation](install/README.md) and [verification](verification.md).

Existing 0.1.0-rc.1/rc.2 tags and assets retain their original contents. The [roadmap](roadmap.md) keeps optional Npcap, PCAP import/live capture/analysis, STUN and Nmap outside this release; Wi-Fi remains deferred.

PRs [#10](https://github.com/Super8Four/flowbench/pull/10) and [#21](https://github.com/Super8Four/flowbench/pull/21) merged into main `22f15addf6b0e232bbe6dcf44d57705ce9b546b3`. Its tree matches the tested `010177c5b85802dd89779e3d530965a5867efeba` feature source. This identifies the delivered code; exact CI results and screenshot/render provenance belong in [verification](verification.md), rather than being inferred from a moving main badge.

[Test endpoints](test-endpoints.md), delivered through [PR24](https://github.com/Super8Four/flowbench/pull/24), distinguish captured execution hostname and numeric socket/destination evidence from requested settings and separate web-control context. Historical views do not rewrite records or substitute current-host values. Existing rc.2 downloads remain unchanged.

Profiles/thresholds, retention, authentication and authenticated instance coordination remain pending. Keep the API within a trusted access boundary. Target Pi deployment, hardware power-loss recovery and full requirements/WCAG/audit acceptance remain unverified. See [remaining requirements](requirements.md).
