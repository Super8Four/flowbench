# Install FlowBench

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](../assets/badges/container.svg)](containers.md) [![Architecture](../assets/badges/platform.svg)](containers.md) [![Python](../assets/badges/python.svg)](containers.md) [![SQLite](../assets/badges/storage.svg)](../sqlite-storage.md) [![iPerf3](../assets/badges/engine.svg)](../protocol-tests.md)

Choose the method that fits your host. Use [0.1.0-rc.2](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2) for the current prerelease; older releases remain available.

| Method | Guide | Verification |
| --- | --- | --- |
| Windows native | [Windows](windows.md) | Python 3.12, the checksummed Windows iPerf3 3.22 build, local startup and regression tests |
| Ubuntu native | [Linux](linux.md) | Ubuntu/Python/iPerf build commands are exercised in native container CI; a standalone Linux host install has not been separately tested |
| Ubuntu container, AMD64/ARM64 | [Docker and Compose](containers.md) | Native builds, full tests and persistent-volume lifecycle checks on both architectures |
| Raspberry Pi ARM64 | [Raspberry Pi](raspberry-pi.md) | ARM64 images tested in CI; physical Pi deployment/performance untested |

- [Configuration, ports and data](configuration.md)
- [Upgrades, backups, rollback and removal](maintenance.md)
- [Troubleshooting](troubleshooting.md)
- [Verification](../verification.md) and [release notes](../release-0.1.0-rc.2.md)

The API has no login. Keep the web port local or behind authenticated access. All examples retain loopback defaults; installation does not configure a firewall or make a test server publicly reachable. Run one app process per archive.

The SVG logo and PNG fallback are included with source and container downloads. There is no separate database server, one-click installer, Windows container image or published container registry image.
