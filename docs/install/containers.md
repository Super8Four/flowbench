# Docker and Compose

[Installation index](README.md) · [Configuration](configuration.md) · [Maintenance](maintenance.md)

Use a Linux Docker Engine with Compose v2. Follow [Docker's Ubuntu installation guide](https://docs.docker.com/engine/install/ubuntu/) if they are missing. The images use Ubuntu 24.04 and support native `linux/amd64` and `linux/arm64`.

## Build the release source

```sh
git clone --branch v0.2.0-rc.1 --depth 1 https://github.com/Super8Four/flowbench.git
cd flowbench
docker compose config --quiet
docker compose build --build-arg VCS_REF="$(git rev-parse HEAD)"
docker compose up -d --wait
curl --fail http://127.0.0.1:5000/api/ready
```

These commands build the tagged prerelease. Record `git rev-parse HEAD` and match it to the release metadata. For development main or historical tags, use [version selection](README.md#choose-a-source-version).

A native build selects the host architecture. The supplied Compose file runs non-root with a read-only root, writable `/data` volume, `/tmp` tmpfs and dropped capabilities. Web access defaults to host loopback port 5000.

## Load a prebuilt release image

The 0.2.0-rc.1 images include the measurement, diagnostic and report features described in these guides.

Download the matching architecture's `.tar.gz`, `.image.json` and `.SHA256SUMS` from the [release](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1). Keep them together in a new directory. Example for ARM64:

```sh
sha256sum -c flowbench-0.2.0-rc.1-linux-arm64.SHA256SUMS
gzip -dc flowbench-0.2.0-rc.1-linux-arm64.tar.gz | docker load
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

## Probe and map availability

Release images include dnspython, numeric route tools and native ping. Dropped capabilities remain in place; a host that denies ICMP produces explicit unavailable/indeterminate evidence. Do not add privileged mode or capabilities just to turn that state into a result. Local clock-service queries can be unavailable inside an ordinary container even though its system clock is shared with the host.

The image includes the offline MMDB reader, not a geographic database. An independently licensed file at `/data/geolocation/dbip-city-lite.mmdb` remains in the existing `iperf_data` volume. The app performs no dataset download or online geolocation request. For another path, set `FLOWBENCH_GEO_DB` explicitly in the service environment and provide readable local data. `.env` alone does not pass arbitrary variables into the container. See [offline location setup](../preflight.md#optional-offline-public-ip-locations). Optional actual Flent CLI tools are also separate; see [Flent setup](../flent.md).
