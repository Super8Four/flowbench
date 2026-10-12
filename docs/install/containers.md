# Docker and Compose

[Installation index](README.md) · [Configuration](configuration.md) · [Maintenance](maintenance.md)

Use a Linux Docker Engine with Compose v2. Follow [Docker's Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/) if they are missing. The images use Ubuntu 24.04 and support native `linux/amd64` and `linux/arm64`.

## Build from source

```sh
git clone https://github.com/Super8Four/flowbench.git
cd flowbench
git switch --detach v0.1.0-rc.2
docker compose config --quiet
docker compose build --build-arg VCS_REF="$(git rev-parse HEAD)"
docker compose up -d --wait
curl --fail http://127.0.0.1:5000/api/ready
```

A native build selects the host architecture. The supplied Compose file runs non-root with a read-only root, writable `/data` volume, `/tmp` tmpfs and dropped capabilities. Web access defaults to host loopback port 5000.

## Load a release image

Download the matching architecture's `.tar.gz`, `.image.json` and `.SHA256SUMS` from the [release](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2). Keep them together in a new directory. Example for ARM64:

```sh
sha256sum -c flowbench-0.1.0-rc.2-linux-arm64.SHA256SUMS
gzip -dc flowbench-0.1.0-rc.2-linux-arm64.tar.gz | docker load
docker image inspect iperf-workspace:local --format '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose up -d --no-build --wait
```

Use `amd64` filenames for AMD64 hosts. Verify the architecture, version and revision against the release metadata. `iperf-workspace:local` is the compatibility image tag; it is loaded locally, not pulled from a registry. The image archive contains no user volume.

## Operate

```sh
docker compose logs --tail=100 app
docker compose stop app
docker compose up -d --wait
```

Finish/stop tests before planned shutdown. Enabled listener settings resume at startup. Keep the same Compose project name and `iperf_data` volume during upgrades. Do not add `--volumes` to removal commands or prune volumes containing history.

These are Linux images. Windows/macOS Docker backends were not separately tested. Physical Pi operation is covered by the [ARM64 caveats](raspberry-pi.md).
