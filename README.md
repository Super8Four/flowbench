# FlowBench

<picture>
  <source media="(prefers-color-scheme: dark)" type="image/svg+xml" srcset="app/static/brand/flowbench-logo-dark.svg">
  <img src="app/static/brand/flowbench-logo.svg" alt="FlowBench" width="520">
</picture>

**Measure. Compare. Verify.**

Run network tests from a browser and keep the evidence in a portable SQLite archive. FlowBench combines iPerf3 throughput, DNS and ICMP checks, saved comparisons, and PDF/Word reports. Tests execute on the FlowBench host; the browser controls them.

[![Release](docs/assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1) [![Native CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Windows and source CI](https://github.com/Super8Four/flowbench/actions/workflows/quality.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/quality.yaml?query=branch%3Amain)

[Download 0.2.0-rc.1](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1) · [Install](docs/install/README.md) · [Release notes](docs/release-0.2.0-rc.1.md) · [Documentation](#documentation)

![FlowBench test page with synthetic endpoint evidence](docs/assets/endpoints-desktop.jpg)

*Actual browser capture with disposable synthetic evidence. Screenshot source and verification limits are recorded in [verification](docs/verification.md).*

## What it does

- Run TCP, UDP, bidirectional and bounded sequential iPerf3 tests; capture incoming sessions and retain local and available peer output.
- Record DNS responses and ICMP observations, with explicit timeout, unavailable and incomplete states. Run again creates a fresh attempt linked to the saved source.
- Compare completed tests over their actual elapsed intervals. Group before/after tests in projects, select baselines, and add notes, tags and timeline events.
- Inspect reported socket endpoints, route observations and local clock evidence. Optional offline maps use a separately supplied geographic database.
- Export JSON/XML evidence or generate PDF/Word reports with redaction enabled by default. Optional Linux Flent RRUL uses separately installed tools.

## Start with Docker

Use Docker Engine and Compose v2 on a compatible Linux host. The release provides native AMD64 and ARM64 images; a physical Raspberry Pi deployment has not been tested.

Build the release source:

```sh
git clone --branch v0.2.0-rc.1 --depth 1 https://github.com/Super8Four/flowbench.git
cd flowbench
docker compose config --quiet
docker compose build --build-arg VCS_REF="$(git rev-parse HEAD)"
docker compose up -d --wait
curl --fail http://127.0.0.1:5000/api/ready
```

Open **http://127.0.0.1:5000**. The web port defaults to localhost. To use a prebuilt image, download its archive, metadata and checksum list from the [release](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1), verify them, then follow [container installation](docs/install/containers.md). Native [Windows](docs/install/windows.md) and [Linux](docs/install/linux.md) instructions are also available.

## Run a measurement

Start `iperf3 -s` on an authorized peer, then select its address, protocol and direction in **Test & export**. New throughput tests default to bidirectional. **Both** runs TCP and UDP separately. Stop keeps the evidence already captured.

Choose **DNS lookup** or **ICMP echo check** for standalone observations. In **History**, open saved evidence or use **Run again** for a new attempt. **Compare tests** keeps throughput curves and diagnostic outcomes distinct; **Before & after projects** records the baseline and change being evaluated.

The **Test endpoints** panel separates measured sockets from requested settings and web-control context. A container address or hostname does not prove a public egress address, physical host or NAT mapping. Missing replies and unverified clock quality remain explicit.

## Keep your history

SQLite lives at `/data/runs/archive.sqlite3` in the persistent `iperf_data` volume. Keep that volume during upgrades. Stop the app before backing up the complete archive, including any WAL/SHM sidecars.

Older JSON/JSONL archives import once without changing their source files. Read [migration, backup and rollback](docs/sqlite-storage.md) before upgrading or moving hosts. No separate database server is required.

## Support and limits

This is a prerelease. The API has **no login**; keep it on localhost or behind authenticated access. Published image checks establish native AMD64/ARM64 packaging and tests, not physical Pi performance or production deployment. Exact results and remaining acceptance work are linked in [verification](docs/verification.md).

Windows does not support UDP bidirectional with the supplied engine; use upload/download or a native Linux image. Flent needs optional Linux tools and an authorized Netserver peer. Geographic data is not bundled or downloaded automatically. Profiles/thresholds, retention, authentication and instance coordination remain planned.

[Npcap, PCAP analysis/live capture and Nmap profiles](docs/roadmap.md) are future work. They are not included in this release. Basic iPerf, ping and route checks remain driver-free.

## Documentation

- [Installation and configuration](docs/install/README.md) · [Troubleshooting](docs/install/troubleshooting.md)
- [TCP, UDP and Both](docs/protocol-tests.md) · [Incoming capture](docs/incoming-listener.md)
- [DNS, ICMP and reruns](docs/network-checks.md) · [Route, clock and offline maps](docs/preflight.md)
- [Projects, matrices and comparisons](docs/measurement-workflows.md) · [Test endpoints](docs/test-endpoints.md)
- [PDF/Word reports](docs/reports.md) · [Optional Flent](docs/flent.md)
- [Archive migration and rollback](docs/sqlite-storage.md) · [Requirements and roadmap](docs/requirements.md)
- [Changelog](CHANGELOG.md) · [Release notes](docs/release-0.2.0-rc.1.md) · [Brand assets](docs/brand-assets.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)
