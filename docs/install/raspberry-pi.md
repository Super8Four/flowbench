# Raspberry Pi ARM64

[Installation index](README.md) · [Container guide](containers.md)

Use a **64-bit ARM64** host. `uname -m` should report `aarch64`; 32-bit ARM is not supported by these image assets. Ubuntu 24.04 ARM64 is the image's verified base. Other Pi operating systems and a physical Pi deployment have not been tested here.

## Choose 0.2.0-rc.1

Install a suitable Docker Engine/Compose v2 for your OS, then use the **0.2.0-rc.1** prerelease. Choose either the [tagged source build](containers.md#build-the-release-source) on an ARM64 host or the [prebuilt image procedure](containers.md#load-a-prebuilt-release-image). Both use the `v0.2.0-rc.1` source tag; main is a separate development choice described in [version selection](README.md#choose-a-source-version).

For the prebuilt image, download these three files from the [0.2.0-rc.1 release](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1):

- `flowbench-0.2.0-rc.1-linux-arm64.tar.gz`
- `flowbench-0.2.0-rc.1-linux-arm64.image.json`
- `flowbench-0.2.0-rc.1-linux-arm64.SHA256SUMS`

Verify the checksum list before loading the archive. Use the Compose file from the same tagged checkout, even when skipping the build. Before starting, follow the container guide's inspection command and confirm `arm64`, version `0.2.0-rc.1` and source revision `10c1427c147e35e8c81a329894832e0faf72d76e` against the release metadata. The compatibility image tag is `iperf-workspace:local`; do not load the AMD64 archive.

## Data and device checks

Keep the data volume on a reliable writable local filesystem. Before upgrading, follow [maintenance](maintenance.md), back up the whole archive with the app stopped, and retain the existing Compose project name and `iperf_data` volume. Storage reliability, thermals, CPU capacity and actual network throughput need testing on the device; ARM64 CI is not a Pi performance result.

Keep web and iPerf publication on loopback until you choose the intended access boundary. There is no app login or automatic firewall configuration. Docker versions before 28 have a documented [loopback-publication limitation](https://docs.docker.com/engine/network/port-publishing/). No SSH access, deployment or device configuration is performed by these instructions.

DNS depends on the installed Python dependency and a selected reachable resolver; ICMP and route evidence depend on the device's tools and permissions. No capability or time-service changes are made automatically. Geographic data remains optional and separately licensed in the persistent data volume. None of the release features establishes physical Pi acceptance; verify on a disposable archive and controlled peer before relying on device measurements.
