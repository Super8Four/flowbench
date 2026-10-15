# FlowBench installation and operation

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

These instructions cover **0.2.0-rc.1**, including standalone checks, reruns, endpoints, route/clock evidence and measurement reports. The [installation index](install/README.md) has platform-specific steps. Older rc.1/rc.2 downloads retain their original contents.

## Ubuntu container

Use Docker Engine and Compose v2 on a compatible Linux host. Build the tagged source using the [README](../README.md#start-with-docker), or download the matching native AMD64/ARM64 archive, metadata and checksum list from the [release](https://github.com/Super8Four/flowbench/releases/tag/v0.2.0-rc.1).

Verify checksums before loading. Example for ARM64:

```sh
sha256sum -c flowbench-0.2.0-rc.1-linux-arm64.SHA256SUMS
gzip -dc flowbench-0.2.0-rc.1-linux-arm64.tar.gz | docker load
docker image inspect iperf-workspace:local --format '{{.Architecture}} {{index .Config.Labels "org.opencontainers.image.version"}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose up -d --no-build --wait
```

Use the release's Compose file and compare architecture, version and source revision with its metadata. The compatibility tag remains `iperf-workspace:local`; no registry image is published. Images use non-root UID/GID `10001:10001`, a read-only root, `/tmp` tmpfs, dropped capabilities and a writable `/data` volume. Image archives do not include user volumes or local credentials. Physical Pi operation remains untested.

## Configuration and reachability

| Setting | Default | Purpose |
| --- | --- | --- |
| `WEB_PORT` (Compose substitution) | `5000` | Host web port, bound to loopback |
| `IPERF_PORT` (Compose substitution) | `5201` | Host TCP/UDP mapped port; keep listener's container port at 5201 |
| `IPERF_BIND_ADDRESS` (Compose substitution) | `127.0.0.1` | Host publication address |
| `RUNS_DIRECTORY` | `/data/runs` container; `instance/runs` local | Writable archive directory |
| `IPERF_PATH` | Bundled container engine; local bin/PATH lookup | Executable, checked at startup |
| `FLOWBENCH_GEO_DB` | `/data/geolocation/dbip-city-lite.mmdb` | Optional licensed local MMDB file; missing data leaves locations unavailable |
| `FLOWBENCH_RESUME_LISTENER` | `1` | Set `0` for maintenance commands to suppress saved listener startup; does not change saved enable state |

The web server and iPerf listener are independent. Client tests require no inbound iPerf publication. For an incoming peer, choose the listener bind deliberately: `127.0.0.1` accepts clients inside that container only; `0.0.0.0` allows the container's IPv4 interfaces to receive the existing mapping. Listener settings do not change host publication/firewall rules. Configure external reachability only after approving the access boundary. The API is unauthenticated. [Docker documents additional loopback publication limits on Engine versions before 28](https://docs.docker.com/engine/network/port-publishing/).

Read status at `/api/status`, health at `/api/health` and readiness at `/api/ready`. Failed archive capture disables readiness until repair/restart. The UI shows the archive path, engine and version on Configuration. `/api/checks` separately reports DNS/ICMP capability hints without sending a network capability probe. Missing iPerf can make `/api/ready` return 503 while a supported standalone check remains usable.

## Tests, directions and evidence

Use an authorized controlled peer, not an arbitrary public endpoint. `iperf3 -s` stays available for repeated tests; `-s -1` accepts only one and cannot serve a complete Both job. Directions always use the client perspective: upload is client to server, download is server to client, bidirectional runs both directions together. Both runs TCP **then** UDP with distinct identities and charts. UDP bandwidth is per stream; start with a bounded rate, such as `2M`, for checks.

Live final averages use reported receiver evidence where available. Interval peaks are separate from final averages. Missing data is displayed as unavailable rather than zero. Remote output requires the peer to supply it. [Route and clock preparation](preflight.md) runs once before each outgoing test, with at most 16 hops/20 seconds for route capture and a separate bounded local time-service query. Probe RTTs remain separate from iPerf measurements; local UTC timestamps are not independently NTP-verified.

History imports saved/native JSON and compatible XML; individual files are limited to 20 MiB, preset catalogs to 8 MiB. Imports are additive, remap local IDs and skip repeated identical evidence. Both/comparison envelopes preserve group provenance. Keep full exports with raw logs; summary-only reports cannot recreate evidence. Public presets are bundled reference data, not checked live; custom presets are editable and portable through JSON.

## DNS, ICMP and reruns

Use DNS checks for explicit questions through system-configured resolver addresses or a chosen numeric resolver. Supported query types, one-to-five query bounds and UDP-to-TCP fallback evidence are documented in [network checks](network-checks.md). No public resolver is silently substituted. An NXDOMAIN or SERVFAIL response is recorded DNS evidence; it does not mean a transport timeout or successful name resolution.

ICMP checks send one through sixty bounded native echo probes, resolving a hostname once and retaining the selected destination. Unknown sends/replies leave the percentage unavailable. A strict RTT bound such as `<1 ms` is not a numeric average sample. ICMP echo nonresponse is separate from application or iPerf UDP loss. Native tools, permissions and parsable output vary by host; the app does not broaden privileges to obtain results.

Checks retain one read-only local clock observation but do not automatically capture a route. Compare terminal checks of the same type and review capture-status/settings caveats. Report redaction is enabled by default; full JSON/XML archives can contain resolver, endpoint, answer, notes and raw-output details.

**Run again** starts from validated saved configuration and creates new IDs, UTC creation timestamps and source lineage. For ordinary iPerf, Both, matrix and standalone checks, the explicit click starts the attempt; use **Load settings** to review or edit a draft first. It copies configuration notes/metadata but clears captured results, logs and timeline observations. Incoming reruns require fresh confirmation in a review panel, then initially wait for a client for at most 60 seconds; accepted traffic retains the existing server hard deadline. Flent also requires a review-panel confirmation for fresh Netserver consent. A lost-response Retry reuses the exact attempt's request identity, while a new action gets a new identity. Restart never resumes or automatically retries interrupted traffic.

## Optional offline map data

Current-main images/dependencies include the MMDB reader but no dataset. Supply a licensed **DB-IP City Lite MMDB** yourself, retaining its required attribution. The default `/data/geolocation/dbip-city-lite.mmdb` lives in the existing persistent volume. Native installations can set an absolute `FLOWBENCH_GEO_DB` path in `.env`. A different Compose path needs an explicit service-environment entry and readable local data; an unused `.env` variable is not automatically forwarded into the container.

The app makes no online geolocation, tile or dataset-download requests. Private/special addresses are excluded from lookup. The ordered topology remains useful without locations, and public-IP coordinates are approximate network context rather than device GPS or a proven physical route. Follow [the dataset, licensing and attribution guide](preflight.md#optional-offline-public-ip-locations) before supplying data. Local clock quality can remain unavailable in a container; system UTC is not independently NTP verified.

## Keep and back up the archive

Finish/stop active tests, then stop the app. Keep the Compose project name and volume when upgrading. **Do not run `docker compose down --volumes`** when retaining history.

```sh
docker compose stop app
container_id=$(docker compose ps --all --quiet app)
docker cp "$container_id:/data" ./flowbench-backup-2026-10-12
```

Choose a new destination for each backup, outside the data volume. Copy all files, including `archive.sqlite3`, any WAL/SHM sidecars and legacy sources. Never copy only a running database or delete its journal sidecars. Start again with `docker compose up -d --wait`.

Restore into a **fresh** archive/volume, not over newer history. Keep an independent untouched backup. Set ownership `10001:10001` for the container's restored files, start one app and verify IDs, notes, logs, metrics, projects/baselines, checks, source lineage, presets and listener settings. A restored enabled listener resumes unless maintenance startup suppression is set. See [SQLite rollback](sqlite-storage.md#rollback) before using an older application.

A complete stopped SQLite backup retains local request mappings, which prevent duplicate execution after a lost response. JSON/XML/project imports preserve evidence and remap lineage, but never register imported launch tokens. Older releases can reject new record kinds; neither a legacy snapshot nor schema 1 guarantees downgrade compatibility. Preserve both the pre-upgrade backup and current archive, rehearse on a fresh copy, and never delete evidence to make older code start.

## Local development

Use the tagged release checkout before installing its requirements. Select main explicitly only for development. Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\install-iperf.ps1
.\scripts\start.ps1
```

On Linux, use a Python virtual environment and a compatible iPerf3 executable. The container pins 3.22 because older distribution packages can lack full streaming JSON. `requirements.txt` includes dnspython and the optional geographic reader; it does not install a geographic dataset or the optional Flent CLI tools. Start one Waitress process from `wsgi:app`, bound to loopback, without a debugger/reloader. `.env.example` lists local overrides; `.env` is ignored and excluded from the image build.

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
| Browser start response is lost | Refresh history/status or use the exact attempt's offered Retry; changed intent needs a new action/request identity |
| DNS unavailable / ICMP percentage missing | Read typed attempt/status evidence; check resolver/tool availability without substituting public resolvers or inventing unknown send counts |
| Incoming rerun client cannot connect | Wait through preparation for native Running, then connect before the initial idle timeout; a rerun is one test, not persistent listener enable |
| Route, clock or locations unavailable | Keep captured throughput; inspect tool/provider/data availability. Clock verification and geographic coordinates are never inferred |

Run the selected virtual environment's `python -m unittest discover -s tests -v` with a verified engine. `python scripts/check-source.py` additionally requires Node.js for JavaScript syntax checks and checks local documentation links; [the testing map](testing.md) lists focused browser, native DNS/ICMP, Flent and report-render gates. Docker checks use `python3 scripts/check-container.py` with disposable containers/volume; do not point them at the production Compose project. See [verification](verification.md) for checked and unverified scope.
