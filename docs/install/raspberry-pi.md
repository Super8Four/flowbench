# Raspberry Pi ARM64

[Installation index](README.md) · [Container guide](containers.md)

Use a **64-bit ARM64** host. `uname -m` should report `aarch64`; 32-bit ARM is not supported by these image assets. Ubuntu 24.04 ARM64 is the image's verified base. Other Pi operating systems and a physical Pi deployment have not been tested here.

Install a suitable Docker Engine/Compose v2 for your OS, then follow the [container guide](containers.md) using the **arm64** release archive or a native source build. Do not load the AMD64 image. Confirm the loaded image's architecture and version before starting it.

Keep the data volume on a reliable writable local filesystem. Back up the whole archive with the app stopped. Storage reliability, thermals, CPU capacity and actual network throughput need testing on the device; ARM64 CI is not a Pi performance result.

Keep web and iPerf publication on loopback until you choose the intended access boundary. There is no app login or automatic firewall configuration. Docker versions before 28 have a documented [loopback-publication limitation](https://docs.docker.com/engine/network/port-publishing/). No SSH access, deployment or device configuration is performed by these instructions.
