# FlowBench

The current main source (`0.2.0-dev`) includes [measurement workflows](docs/measurement-workflows.md), [optional native Flent RRUL](docs/flent.md), and [PDF/Word reports](docs/reports.md). The latest published prerelease remains **0.1.0-rc.2**; its source tag and downloads are unchanged.

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](docs/assets/badges/container.svg)](docs/setup-and-testing.md) [![Architecture](docs/assets/badges/platform.svg)](docs/setup-and-testing.md) [![Python](docs/assets/badges/python.svg)](docs/setup-and-testing.md) [![SQLite](docs/assets/badges/storage.svg)](docs/sqlite-storage.md) [![iPerf3](docs/assets/badges/engine.svg)](docs/protocol-tests.md)

<picture>
  <source media="(prefers-color-scheme: dark)" type="image/svg+xml" srcset="app/static/brand/flowbench-logo-dark.svg">
  <img src="app/static/brand/flowbench-logo.svg" alt="FlowBench" width="520">
</picture>

**Measure. Compare. Verify.**

Run iPerf3 tests, capture incoming sessions, and compare saved results. FlowBench keeps settings, measurements, logs and notes in a portable SQLite archive.

![FlowBench test page](docs/assets/integrated-test-desktop.jpg)

*Example screen with synthetic test data. [More screenshots](docs/verification.md).*

## Start on Ubuntu

**[Installation wiki](docs/install/README.md)** — Windows, native Linux, Docker/Compose and Raspberry Pi ARM64.

With Docker Engine and Compose v2 installed, run:

```sh
docker compose build
docker compose up -d --wait
curl --fail http://127.0.0.1:5000/api/ready
```

Open **http://127.0.0.1:5000**. [0.1.0-rc.2 downloads](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2) include source, native AMD64/ARM64 container images and checksums. This is a prerelease. See [setup](docs/setup-and-testing.md) for loading an image, Windows installation, port mappings and troubleshooting.

## Run a test

1. Start `iperf3 -s` on a server you have permission to test. Tests run from the FlowBench host.
2. Choose TCP, UDP or **Both** and a direction. New tests default to bidirectional. Both runs TCP then UDP and keeps their results separate.
3. Start the test, review throughput and logs, then export JSON/XML or save notes. Stopping keeps the output already captured.
4. Use **History** to search/import sessions and **Compare tests** to add completed tests, choose their colors and compare actual elapsed measurements. Shorter tests end at their captured duration; stored rates keep their original numeric units while the interface scales bps through Tbps.

To receive tests, enable the listener in **Configuration**. Incoming iPerf3 clients are saved automatically. One incoming test can run alongside one outgoing test; overlap may affect measurements and is labeled. The listener restarts with the app until you turn it off. See [incoming capture](docs/incoming-listener.md).

## Keep your history

The archive lives at `/data/runs/archive.sqlite3` in the persistent `iperf_data` volume. Keep that volume when replacing the image. Back up the complete archive with the app stopped.

Older JSON/JSONL archives import once without changing the source files. See [migration, backup and rollback](docs/sqlite-storage.md) before upgrading or moving hosts.

## Limits

The API has **no login**. Keep it on localhost or behind authenticated access. Listener bind settings do not change host port mappings or firewall rules.

The Windows engine does not support UDP bidirectional; use a Linux image or select upload/download. Actual Flent execution requires optional Linux tools and a controlled Netserver peer. Public server listings may be unavailable or have different limits. Profiles/thresholds, retention, authentication and instance coordination remain planned. Pi deployment and broader acceptance are still unverified.

## Documentation

- [Setup and troubleshooting](docs/setup-and-testing.md)
- [TCP, UDP and Both](docs/protocol-tests.md)
- [Route and clock preparation](docs/preflight.md)
- [Incoming capture](docs/incoming-listener.md)
- [Backup and rollback](docs/sqlite-storage.md)
- [Brand assets](docs/brand-assets.md), [verification](docs/verification.md) and [remaining requirements](docs/requirements.md)
- [Changelog](CHANGELOG.md), [release notes](docs/release-0.1.0-rc.2.md) and [third-party notices](THIRD_PARTY_NOTICES.md)
