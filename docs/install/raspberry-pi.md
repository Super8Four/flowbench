# Raspberry Pi ARM64

[Installation index](README.md) · [Container guide](containers.md)

Use a **64-bit ARM64** host. `uname -m` should report `aarch64`; 32-bit ARM is not supported by these image assets. Ubuntu 24.04 ARM64 is the image's verified base. Other Pi operating systems and a physical Pi deployment have not been tested here.

Install a suitable Docker Engine/Compose v2 for your OS, then follow the [container guide](containers.md) using an **arm64 current-main source build** for the merged features, or the older **arm64 rc.2 archive** to reproduce that immutable prerelease. The existing rc.2 image does not contain the new checks, reruns, route/clock or report workflows. Do not load the AMD64 image. Confirm the loaded image's architecture and version before starting it.

Keep the data volume on a reliable writable local filesystem. Back up the whole archive with the app stopped. Storage reliability, thermals, CPU capacity and actual network throughput need testing on the device; ARM64 CI is not a Pi performance result.

Keep web and iPerf publication on loopback until you choose the intended access boundary. There is no app login or automatic firewall configuration. Docker versions before 28 have a documented [loopback-publication limitation](https://docs.docker.com/engine/network/port-publishing/). No SSH access, deployment or device configuration is performed by these instructions.

DNS depends on the installed Python dependency and a selected reachable resolver; ICMP and route evidence depend on the device's tools and permissions. No capability or time-service changes are made automatically. Geographic data remains optional and separately licensed in the persistent data volume. None of the merged features establishes physical Pi acceptance; verify on a disposable archive and controlled peer before relying on device measurements.
