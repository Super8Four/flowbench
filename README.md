# iPerf workspace

An AdminLTE 4 web interface for iPerf3, backed by Python and Flask. Run TCP/UDP client tests or a one-test server session, watch live output and throughput, and keep an archive for later verification. AdminLTE and Bootstrap assets are bundled locally. See `THIRD_PARTY_NOTICES.md` for versions and licenses.

The entire workspace uses AdminLTE's dark mode, with a shared charcoal palette for navigation, cards, forms, tables, results, and logs, following the [Dashboard v2 example](https://adminlte.io/themes/v4/index2.html). Dark mode is applied before the page renders, including on reload; new controls inherit the same theme.

For installation and use on another machine, start with [Setup and testing guide](docs/setup-and-testing.md). It covers Ubuntu Docker deployment, client/server testing at either end, sharing JSON/XML evidence, custom presets, backups, and troubleshooting. An [importable example server preset](docs/examples/server-presets.json) is included; replace its example address before use.

The [requirements and container goal](docs/requirements.md) describe the focused pages, two/three-test overlays, route capture, and Ubuntu AMD64/ARM64 operation. [Project state](docs/project-state.md) separates current implementation from completed verification.

## Start locally on Windows

If the virtual environment is already set up, run:

```powershell
.\scripts\start.ps1
```

Open http://127.0.0.1:5000. The app listens on loopback. Stop it with Ctrl+C. Use one Waitress process; its threads share the session manager. The archive prevents a second process from opening it for writing. Run without Flask's debugger/reloader.

For a fresh checkout:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\install-iperf.ps1
.\scripts\start.ps1
```

The installer pins the community Windows iPerf3 3.22 build and verifies its release SHA-256. You can instead install your preferred iPerf3 build and set `IPERF_PATH` to its executable in `.env` or the environment. Otherwise the app checks `bin/` and then PATH. Restart after changing the executable. Linux can use its distribution's `iperf3` package.

## Run and retain tests

1. Run `iperf3 -s` on the target machine, then enter that machine's IP/hostname and port in the web interface. Tests originate from the machine running this app, rather than from the browser.
2. Choose TCP or UDP, upload/download/bidirectional, duration, and stream count. UDP includes a bandwidth target per stream.
3. Label the test and record conditions or the fix in Notes. Every session is saved immediately; export is optional.
4. Watch throughput and all captured stdout, stderr, and app events. Filter by source or search the log. Stop a session when needed.
5. Open any saved run to replay its logs, edit its label/notes, load its settings for another test, or export JSON/XML.
6. Open **Compare tests** and choose a baseline, a follow-up, and optionally a third test. The comparison calculates changes in throughput, retransmits, jitter, and loss when iPerf reports them. Differences in settings are flagged. Exporting a comparison includes all selected original runs, their logs, route evidence, and changes from baseline.

The Windows build was verified for TCP upload, download, bidirectional, parallel streams, UDP upload/download, and one-test server sessions. UDP bidirectional is disabled on Windows because the tested Cygwin build stalls in that mode. Platforms that do not report TCP retransmissions display no value for that metric; absent metrics are not turned into zeroes. Older builds without JSON streaming use live verbose text output; their export still retains all raw output and parsed throughput.

The throughput graph uses a dark background with smooth blue upload and green download curves and gradient fills. The curves interpolate measured samples without introducing extra measurement points; hover a sample for its exact value. Axes use elapsed seconds and Mbps.

**Previous tests** presents a compact dark table with status badges, maximum upload/download speeds, final averages, measured runtime, and blue/green throughput sparklines. Peaks come from recorded aggregate intervals, avoiding double counting parallel streams. Runtime excludes connecting or waiting for a client; partial tests show the time actually measured. Summary-only imports can show an average and runtime but cannot establish peaks or a trend. Missing values remain blank (—). Each sparkline uses its own scale; long tests keep interval extrema in a bounded preview. Opening or exporting a test retains every original interval and log. The same peak/runtime overview is included in JSON/XML exports.

Use **Chart results source** to select **Local iPerf results**, **Remote server results**, or **Compare local & remote**. Client sessions request server output with `--get-server-output`. When supplied, native `server_output_json` or `server_output_text` is parsed separately and included as `peer` in exports/API results. In comparison view, remote curves use dashed lines, and a table shows each end's reported throughput/retransmissions/jitter/loss where available. Expand **Remote server raw output** to inspect the original peer output. Completed older sessions/imports can use the same view if they contain returned server output. The remote endpoint is the actual target of that session; it is not a second background test.

Server output normally arrives after a test finishes. Some public servers omit it or report text that does not contain recognizable metrics; the app keeps their raw output and shows missing values rather than estimating them. Both sides use the client perspective for direction labels. A server's text figures are rounded, and its interval clock differs from the client's, so differences between the two reports alone do not prove packet loss or a network fix. Local server sessions already show server measurements; no separate remote-client output is requested for them.

Server mode listens on the selected port and optional local bind address, accepts one test, then exits. The peer's actual protocol, duration, streams, and direction are recorded from iPerf output. Waiting for a client is bounded by the configured idle timeout. An idle session has no performance metrics to compare.

## Public and custom server presets

Choose a public server in Test configuration, or search the **Server presets** table by host, country, city, continent, or provider. Loading a preset fills the form; it does not start a test. Settings stay editable. Listed port ranges, TCP/UDP, upload/download, and IPv6 support are shown alongside the defaults. Warnings highlight options outside the listing. Use the IP version selector to force IPv4 or IPv6 when needed. Bidirectional support is not inferred from reverse mode.

The bundled catalog contains 184 servers fetched from [iPerf3 Server List's official JSON export](https://export.iperf3serverlist.net/listed_iperf3_servers.json) on October 11, 2026 UTC (October 10 locally). **Refresh public list** downloads the current catalog on demand and replaces public entries while retaining custom presets. Offline/invalid refreshes retain the existing catalog. The app never downloads a list at startup or automatically tests public servers. Listed capacity is provider/catalog information, not measured throughput. Public servers can be busy or unavailable.

To create a custom preset, set the client test configuration, open **Save the current test settings as a custom preset**, and supply a name, optional port range, and preset notes. Save it, or load an existing custom preset and choose **Update selected custom preset**. Custom presets preserve all supported client settings. Preset notes describe the server; Test configuration Notes are retained with each test. Removing a custom preset does not remove saved tests.

**Export presets JSON** downloads the complete public/custom catalog. **Import server JSON** accepts that file, the official parsed `listed_iperf3_servers.json`, or the unparsed `unparsed_iperf3_servers.json` export. Imports validate the whole file before saving and merge additively; matching kind/name/host/port-range entries update rather than duplicate. Command strings in the unparsed list are parsed only for host/port and are never executed. Files are limited to 8 MiB and 5000 entries. A portable custom list looks like:

```json
{
  "schema_version": 1,
  "presets": [
    {
      "name": "Office uplink",
      "kind": "custom",
      "ports": "5201-5209",
      "notes": "Private LAN server",
      "settings": {
        "target": "192.168.1.100",
        "port": 5201,
        "protocol": "tcp",
        "mode": "download",
        "duration": 10,
        "streams": 1,
        "ip_version": "auto"
      }
    }
  ]
}
```

The catalog is saved beside test data as `RUNS_DIRECTORY/server-presets.json` after a change; until then the bundled catalog is used. Include it when backing up or migrating the archive into Docker.

## Import older test evidence

In **Saved test history**, choose **Import saved tests** and select one or more files. Supported formats are this app's complete session JSON/XML exports, comparison JSON/XML exports (both source tests), a `run.json` metadata backup, and native `iperf3 -J` JSON. Imports preserve original results, logs supplied in the file, settings, notes, and recorded test dates; metrics and charts are recalculated from the saved measurements. Native files must contain `start.test_start`, `start.timestamp.timesecs`, and `end`. Their finish date is estimated from the original start date and configured duration; their full native result is retained. A metadata-only `run.json` does not include the separate `events.jsonl` logs: use full session exports to move all evidence.

Imported tests get new local IDs and an **Imported** badge with source filename, original ID, import date, and SHA-256 provenance. They never overwrite an existing test or launch a process. Re-uploading identical test objects (including equivalent JSON/XML exports) skips duplicates. Completed imported tests with measurements are available on the Comparison page. Unfinished snapshots become interrupted history records. No missing measurements are fabricated. Each file is limited to 20 MiB; malformed files are rejected. XML accepts this app's typed format and rejects DTD/entity declarations.

## Persistent storage and exports

The authoritative archive is **`instance/runs/`**, independent of browser storage and the starter PostgreSQL connection. Each run has its own UUID directory containing:

- `run.json`: settings, notes, timestamps in UTC, status, exit code, interval measurements, iPerf version, command arguments, and native results.
- `events.jsonl`: an append-only log of timestamped stdout, stderr, and app events, with sequence numbers for replay.

Output is written before it is displayed. Metadata is replaced atomically. Completed, failed, cancelled, and interrupted sessions remain available after an app restart. If the app stops abruptly, unfinished records are marked interrupted on the next start. Closing the browser leaves the active test running. The log captures what the launched process emits, including remote output requested with `--get-server-output`; it is not packet capture or a collector for unrelated external iPerf processes. Enable debug output for additional iPerf diagnostics.

Set `RUNS_DIRECTORY` to change the archive location. Relative paths resolve from the app's working directory. Back up the **whole archive directory**, preferably with the app stopped. No automatic deletion or retention limit is applied. Browser local storage holds only selected comparison IDs; it does not hold measurement data.

JSON and XML exports include the same settings, metadata, metrics, intervals, native results, raw stdout/stderr, and complete event history. Exporting a running session produces a snapshot of the data captured so far. XML uses typed `field` and `item` elements to preserve nested objects, arrays, numbers, booleans, and nulls. Strings with characters XML cannot represent use base64 with an explicit encoding attribute.

The existing PostgreSQL configuration, `Measurement` starter model, `init-db` command, and database-only service in `compose.postgres.yaml` are retained for future database integration. Test history currently uses the disk archive; it does not require PostgreSQL or a schema migration. `/api/health` reports the starter database's connection status; `/api/status` reports the iPerf engine, archive path, and active session. `/api/ready` checks the engine and archive manager without a database connection or a full history scan.

## Ubuntu container

Install [Docker Engine and Compose on Ubuntu](https://docs.docker.com/engine/install/ubuntu/), clone this repository, and run:

```sh
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

Open http://127.0.0.1:5000 on the Docker host. If port 5000 is occupied, use `WEB_PORT=8080 docker compose up -d --build` and open port 8080. Use `docker compose stop` to stop cleanly, or `docker compose down` to remove the container while retaining saved data. Updating the checkout and rerunning `docker compose up -d --build` recreates the app using the same archive volume.

The image uses Ubuntu 24.04 LTS, Python 3.12, one Waitress process with eight threads, and upstream iPerf3 3.22. A separate build stage verifies the iPerf source checksum and compiles it; the final image contains no compiler, downloaded source archive, pip cache, or development virtual environment. Source code is owned by root; the app runs as UID/GID `10001:10001`, with a read-only root filesystem, writable `/tmp`, and writable archive volume. SIGTERM stops an active test, saves its output, and closes the archive. The health probe uses `/api/ready`; PostgreSQL is not started or required. The old database-only setup remains available separately with `docker compose -f compose.postgres.yaml up -d`.

**Saved evidence is in the named `iperf_data` volume**, mounted at `/data`, with runs and custom presets under `/data/runs`. Normal stop, restart, rebuild, and container replacement preserve it. Do not use `docker compose down --volumes` unless you intend to delete that archive. The build context excludes your local `.env`, Windows executables, `.venv`, exports, and run archive. Your existing Windows results are not automatically copied or published in the image.

To move evidence using the interface, export your sessions/comparisons as JSON or XML, import them into the container's Previous tests, and separately export/import your server presets JSON. Full exports retain the logs; `run.json` alone does not. To preserve the complete archive and original IDs instead, stop both apps, back up `instance/runs`, then copy its contents into a newly created app container's volume:

```sh
docker compose build app
docker compose create app
container_id=$(docker compose ps --all --quiet app)
docker cp ./instance/runs/. "$container_id:/data/runs/"
docker run --rm --user 0 --volumes-from "$container_id" --entrypoint chown iperf-workspace:local -R 10001:10001 /data
docker compose start app
```

Use this copy procedure only for a fresh container archive; avoid overwriting existing evidence. After migration, confirm history, notes, logs, and custom presets before retiring the old copy. For a complete volume backup, stop the app and copy `/data/runs` out with `docker cp`. Keep the resulting backup outside the container.

Compose requests loopback publication for the default web and iPerf ports. Docker releases before 28.0.0 have a [documented limitation](https://docs.docker.com/engine/network/port-publishing/): machines on the same network segment can reach loopback-published ports. On the verified Docker 26.1.5 Pi host, review the existing access boundary before deployment; no network or security change is part of the current documentation-only work. Server sessions use container port **5201**, published for both TCP and UDP; changing the port in the UI also requires a matching Docker port mapping. To receive tests from your LAN, set `IPERF_BIND_ADDRESS` to the Docker host's LAN address when starting Compose, and keep port 5201 in the UI. `IPERF_PORT` changes the host-side port only. Binding in the test form refers to an address **inside the container**; leave it automatic unless needed. Client tests originate from the container; `127.0.0.1` as a target addresses the container itself. Use a server's reachable LAN/public address. Docker's bridge network can affect measurements, so keep container/network settings consistent between baseline and follow-up tests.

The web control API has no authentication. Use localhost or an SSH tunnel for web access; add authentication before exposing it to other machines.

### Build verification and downloadable image

The **Ubuntu container** GitHub Actions workflow builds and runs the complete suite and disposable lifecycle checks on native Ubuntu AMD64 and ARM64 runners. It verifies image architecture, route-tool availability, non-root/read-only startup, health checks, real loopback throughput and remote output, JSON/XML exports, custom presets, persistence, and stopping an active test. Successful jobs package separate **iperf-workspace-linux-amd64** and **iperf-workspace-linux-arm64** archives, retained for 14 days. Load the archive matching the host architecture, then use `docker compose up -d --no-build`. See [requirements](docs/requirements.md) for commands and the distinction between CI execution and Pi validation.

The [verified container build for commit `60ca3c9`](https://github.com/Super8Four/iPerfWeb/actions/runs/38111657080) passed all **39 tests** and the lifecycle checks on an Ubuntu runner. Its amd64 image was **173.0 MiB**, with **53.93 MiB** idle container memory during that check. These are measurements from that runner; server architecture, dependency updates, and workloads can change them. A later authorized read-only SSH inventory verified the Pi hostname `J-RPI5`, `aarch64` architecture, Docker daemon `26.1.5+dfsg1` on `linux/arm64`, and Compose `2.26.1-4`. No application transfer, ARM64 image build, smoke test, or deployment started. The user has paused that work and requested documentation only while the function-change list is pending. The GitHub results above are separate runner evidence, not Pi application verification.

### Test on the Ubuntu server

From your computer, connect using an authorized SSH key or the server's configured authentication:

```sh
ssh gjensen@172.31.20.100
```

Run the following commands **on the server**, using native Docker Engine. Confirm Docker access and the server architecture first:

```sh
uname -m
docker version
docker compose version
python3 --version
```

Docker Engine, the Compose plugin, Git, and Python 3 are required. The account running the checks must have Docker access. For a new checkout:

```sh
git clone https://github.com/Super8Four/iPerfWeb.git
cd iPerfWeb
```

For an existing checkout, enter its directory and run `git pull --ff-only`. Build on the server so the image matches its architecture; use the downloadable amd64 artifact only on an amd64 host (`uname -m` reports `x86_64`).

In a Bash shell, validate Compose, build, and run the automated checks. Keep the logs for later comparison:

```bash
set -euo pipefail
mkdir -p test-artifacts
git rev-parse HEAD | tee test-artifacts/tested-commit.txt
uname -m | tee test-artifacts/server-architecture.txt
docker compose config --quiet
docker build --progress=plain -t iperf-workspace:local . 2>&1 | tee test-artifacts/container-build.log
docker run --rm --entrypoint python --mount "type=bind,source=$(pwd)/tests,target=/tests,readonly" iperf-workspace:local -m unittest discover -s /tests -v 2>&1 | tee test-artifacts/container-tests.log
python3 scripts/check-container.py 2>&1 | tee test-artifacts/container-lifecycle.log
```

Expect `Ran 48 tests` and `OK`, followed by `Container checks passed` with the image size and idle memory. A failed command stops this sequence. Logs stay under the ignored `test-artifacts/` directory and are not committed to GitHub.

The lifecycle check uses uniquely named disposable containers and volumes. It verifies real loopback throughput and remote output, exports, persistence across replacement, and saving an active test on shutdown. It removes only its own test containers and volume; it never opens or removes your Compose archive. Loopback checks verify application behavior; use a separate reachable iPerf server for network performance measurements.

After those checks pass, start the persistent app:

```sh
docker compose up -d --no-build --wait
docker compose ps
curl --fail http://127.0.0.1:5000/api/ready
docker compose logs --tail=100 app
```

Expect a healthy container and `{"ready":true}`. To open the interface from your computer, leave this SSH tunnel running in another terminal:

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:5000 gjensen@172.31.20.100
```

Open http://127.0.0.1:8080 on your computer. This example leaves the server's web port bound to loopback and uses local port 8080 to avoid conflicting with an existing local app.

### Verify saved evidence and a network fix

1. Run a short test against a reachable second machine running `iperf3 -s`. Use its LAN address, rather than `127.0.0.1`. Label the run **Before**, record the conditions in Notes, and confirm the live graph and logs.
2. When it completes, inspect **Remote server results** if the peer returned them. Export the full session once as JSON and once as XML; retain both files outside the container.
3. Run `docker compose down` followed by `docker compose up -d --no-build`. Once healthy, reopen the same saved run and confirm its notes, logs, results, and custom presets remain. Avoid `--volumes`, which deletes the archive.
4. In a separate fresh workspace, import the exported JSON through **Import saved tests** and confirm its logs and results. Import the same JSON again, then its equivalent XML export; both should be recognized as duplicates of the imported test.
5. Apply the network fix, then run **After** with the same target, protocol, direction, duration, streams, and bandwidth setting. Select Before and After in the comparison controls. Inspect the measurements and any settings warnings, then export the comparison as JSON or XML to preserve both source runs and the calculated changes.

Keep the server architecture, tested commit, container/network configuration, and test conditions with your evidence. Container bridge networking and unrelated traffic can affect throughput; compare results under consistent conditions. Successful automated loopback tests alone do not establish that a network issue is fixed.


## Focused pages and route evidence

Use the AdminLTE sidebar submenus for **Test & export**, **Accept incoming test**, **History & import**, and **Compare tests**; **Server presets** has its own page. History Open links to the saved session on Test & export. Save custom presets alongside the test configuration. A preset catalog Load action returns to the test page with its settings.

Finished sessions display colored result cards. The Comparison page overlays two or three tests with individual colors, solid upload curves, dashed download curves, a shared elapsed-time axis, and local/remote report selection. The metric grid and change details preserve missing measurements and settings mismatch warnings.

**Capture route after the test** is selected initially on the client form and can be cleared. Saved presets retain their own selection; existing presets default to off. Route capture runs after successful throughput measurement, is bounded to 16 hops / 20 seconds, and saves raw output, responding addresses, and probe RTTs. An unavailable tool, timeout, or missing response does not change a successful throughput result or establish packet loss. Windows uses `tracert`; the Ubuntu image contains unprivileged `tracepath`. Server sessions skip route capture. API clients opt in with `traceroute: true`.

## Checks and API

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app\static\app.js
```

Tests use a temporary archive and real iPerf3 loopback processes. They cover live output, TCP directions and parallel streams, UDP metrics, server sessions, cancellation, connection failures, input validation, archive recovery/reopening, comparison calculations, and equivalent JSON/XML exports. Integration checks skip when iPerf3 is not installed.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Engine capabilities, storage location, active session |
| `GET /api/ready` | Container readiness: engine available and archive manager healthy |
| `GET /api/runs` | Saved history and active run ID |
| `POST /api/runs/import` | Import saved JSON/XML; JSON envelope with `filename` and `content` text |
| `GET /api/presets[?download=1]` | Read/export public and custom catalog |
| `POST /api/presets` | Save a custom preset with `name`, `settings`, optional `ports`/`notes` |
| `PATCH /api/presets/<id>` | Replace a custom preset |
| `DELETE /api/presets/<id>` | Remove a custom preset; send `{}` as JSON |
| `POST /api/presets/import` | Merge server JSON; envelope with `filename` and `content` |
| `POST /api/presets/refresh` | Fetch the fixed official public JSON URL; send `{}` as JSON |
| `POST /api/runs` | Start a client or one-test server session from JSON settings |
| `GET /api/runs/<id>?after=<sequence>` | Metrics and output after a saved event cursor |
| `POST /api/runs/<id>/stop` | Stop the active session; send `{}` as JSON |
| `PATCH /api/runs/<id>` | Update `label` and/or `notes` |
| `GET /api/runs/<id>/export?format=json\|xml` | Download a complete run |
| `GET /api/comparison?id=<id>&id=<id>[&id=<id>]` | Two/three-test chart and metric report |
| `GET /api/comparison?id=<id>&id=<id>[&id=<id>]&format=json\|xml` | Export all selected source runs and comparison evidence |
| `GET /api/comparison?before=<id>&after=<id>` | Compare two completed tests |
| `GET /api/comparison?before=<id>&after=<id>&format=json\|xml` | Export comparison and both source runs |

Control requests use JSON, reject cross-origin browser writes, and execute validated argument arrays without a shell. One session runs at a time to avoid overlapping tests distorting measurements.
