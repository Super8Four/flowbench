# FlowBench installation and operation

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

## Ubuntu container

Install Docker Engine and Compose v2 using [Docker's Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/) on the approved host. Build from the intended branch/tag using the README commands. Use native `linux/amd64` or `linux/arm64`; choose ARM64 only on a compatible 64-bit host. The runtime is non-root UID/GID `10001:10001`, with a read-only root, `/tmp` tmpfs, dropped capabilities and a writable `/data` volume.

To use the prerelease image instead of building, download its architecture-specific `.tar.gz`, `.image.json` and `.SHA256SUMS` files into a clean directory. Verify the checksum list **before** loading. Example for ARM64:

```sh
sha256sum -c flowbench-0.1.0-rc.2-linux-arm64.SHA256SUMS
gzip -dc flowbench-0.1.0-rc.2-linux-arm64.tar.gz | docker load
docker image inspect iperf-workspace:local --format '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose up -d --no-build --wait
```

The compatibility image tag remains `iperf-workspace:local`. Metadata records its source revision, version, architecture and image ID. Release source archives are GitHub-generated source downloads; they contain no local `.env`, archives or Windows engine. A container archive does **not** include your data volume.

## Configuration and reachability

| Setting | Default | Purpose |
| --- | --- | --- |
| `WEB_PORT` (Compose substitution) | `5000` | Host web port, bound to loopback |
| `IPERF_PORT` (Compose substitution) | `5201` | Host TCP/UDP mapped port; keep listener's container port at 5201 |
| `IPERF_BIND_ADDRESS` (Compose substitution) | `127.0.0.1` | Host publication address |
| `RUNS_DIRECTORY` | `/data/runs` container; `instance/runs` local | Writable archive directory |
| `IPERF_PATH` | Bundled container engine; local bin/PATH lookup | Executable, checked at startup |
| `FLOWBENCH_RESUME_LISTENER` | `1` | Set `0` for maintenance commands to suppress saved listener startup; does not change saved enable state |

The web server and iPerf listener are independent. Client tests require no inbound iPerf publication. For an incoming peer, choose the listener bind deliberately: `127.0.0.1` accepts clients inside that container only; `0.0.0.0` allows the container's IPv4 interfaces to receive the existing mapping. Listener settings do not change host publication/firewall rules. Configure external reachability only after approving the access boundary. The API is unauthenticated. [Docker documents additional loopback publication limits on Engine versions before 28](https://docs.docker.com/engine/network/port-publishing/).

Read status at `/api/status`, health at `/api/health` and readiness at `/api/ready`. Failed archive capture disables readiness until repair/restart. The UI shows the archive path, engine and version on Configuration.

## Tests, directions and evidence

Use an authorized controlled peer, not an arbitrary public endpoint. `iperf3 -s` stays available for repeated tests; `-s -1` accepts only one and cannot serve a complete Both job. Directions always use the client perspective: upload is client to server, download is server to client, bidirectional runs both directions together. Both runs TCP **then** UDP with distinct identities and charts. UDP bandwidth is per stream; start with a bounded rate, such as `2M`, for checks.

Live final averages use reported receiver evidence where available. Interval peaks are separate from final averages. Missing data is displayed as unavailable rather than zero. Remote output requires the peer to supply it. Optional route probes run after throughput, use at most 16 hops/20 seconds, and retain probe RTTs separately; they are not iPerf latency or throughput measurements.

History imports saved/native JSON and compatible XML; individual files are limited to 20 MiB, preset catalogs to 8 MiB. Imports are additive, remap local IDs and skip repeated identical evidence. Both/comparison envelopes preserve group provenance. Keep full exports with raw logs; summary-only reports cannot recreate evidence. Public presets are bundled reference data, not checked live; custom presets are editable and portable through JSON.

## Keep and back up the archive

Finish/stop active tests, then stop the app. Keep the Compose project name and volume when upgrading. **Do not run `docker compose down --volumes`** when retaining history.

```sh
docker compose stop app
container_id=$(docker compose ps --all --quiet app)
docker cp "$container_id:/data" ./flowbench-backup-2026-10-12
```

Choose a new destination for each backup, outside the data volume. Copy all files, including `archive.sqlite3`, any WAL/SHM sidecars and legacy sources. Never copy only a running database or delete its journal sidecars. Start again with `docker compose up -d --wait`.

Restore into a **fresh** archive/volume, not over newer history. Keep an independent untouched backup. Set ownership `10001:10001` for the container's restored files, start one app and verify IDs, notes, logs, metrics, presets and listener settings. A restored enabled listener resumes unless maintenance startup suppression is set. See [SQLite rollback](sqlite-storage.md#rollback) before using an older application.

## Local development

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\install-iperf.ps1
.\scripts\start.ps1
```

On Linux, use a Python virtual environment and a compatible iPerf3 executable. The container pins 3.22 because older distribution packages can lack full streaming JSON. Start one Waitress process from `wsgi:app`, bound to loopback, without a debugger/reloader. `.env.example` lists local overrides; `.env` is ignored and excluded from the image build.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Listener failed / address in use | Stop the conflicting test/process or choose a matching unused port; inspect listener diagnostics, then explicitly restart |
| A second incoming client fails | One accepted session is supported at a time; wait for it to finish. Windows can report a reset instead of "busy" |
| No incoming history | Confirm the client reaches this managed process and its bind/mapping; other independent iPerf servers are not captured |
| UDP/Both bidirectional rejected on Windows | Use native Linux, or select upload/download explicitly |
| Missing remote measurements | Keep local evidence; peers may not supply remote output; no data is fabricated |
| HTTP 503 / archive errors | Stop traffic, retain the archive, inspect free space/permissions and SQLite diagnostics, restore access then restart; never delete the database as a repair shortcut |
| Another writer owns archive | Stop the duplicate app; one process with threads owns each directory |
| Import fails / unsupported schema | Preserve original file, validate a copy and supported envelope; failed transactions do not partially import |
| Browser request times out | Refresh status before retrying a start/enable action; it may have completed on the server |

Run `python -m unittest discover -s tests -v` with a verified engine. Docker checks use `python3 scripts/check-container.py` with disposable containers/volume; do not point them at the production Compose project. See [verification](verification.md) for checked and unverified scope.
