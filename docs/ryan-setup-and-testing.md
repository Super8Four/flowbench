# Ryan's setup and testing guide

This guide covers running the iPerf workspace on an Ubuntu Docker host, testing both ends of a connection, and keeping evidence to compare a network fix. The app records the iPerf3 process it starts, including stdout, stderr, timestamps, settings, measurements, and returned server output. Tests run from the Docker host's container; the browser controls and displays them.

Use your own addresses and SSH account in the examples. `192.168.1.100` represents Ryan's Docker host; `192.168.1.200` represents another iPerf server. These are example addresses, not discovered machines.

## Install and start

Use native Docker Engine on Ubuntu with the Compose plugin. Follow the [official Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/) if Docker is missing. Git, Python 3, and curl are needed for checkout and testing. The account running these commands needs Docker access.

On Ryan's Ubuntu host:

```bash
uname -m
docker version
docker compose version
python3 --version
git clone https://github.com/Super8Four/iPerfWeb.git
cd iPerfWeb
docker compose config --quiet
docker compose up -d --build --wait
docker compose ps
curl --fail http://127.0.0.1:5000/api/ready
```

For an existing checkout, enter its directory and use `git pull --ff-only` instead of cloning again. Build on the host to match its architecture. The downloadable GitHub image is amd64; an ARM host needs its own build.

Expect a healthy container and a readiness response containing `"ready": true`. Compose's [`--wait` option](https://docs.docker.com/reference/cli/docker/compose/up/) waits for service startup and health. The app requires no separate PostgreSQL container.

Open http://127.0.0.1:5000 if the browser is on the Docker host. From another computer, run this tunnel in a terminal and leave it open:

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:5000 ryan@192.168.1.100
```

Open http://127.0.0.1:8080 on that computer. Replace `ryan` with the host's actual SSH account. The app's web API has no login. Compose requests loopback publication, and the tunnel provides remote access. Docker [releases before 28.0.0 can expose loopback-published ports to the same network segment](https://docs.docker.com/engine/network/port-publishing/); review the existing access boundary before deployment on the verified Docker 26.1.5 Pi. The current work changes documentation only.

## Verify the container

In the repository directory on the Docker host, run:

```bash
set -euo pipefail
mkdir -p test-artifacts
git rev-parse HEAD | tee test-artifacts/tested-commit.txt
docker run --rm --entrypoint python --mount "type=bind,source=$(pwd)/tests,target=/tests,readonly" iperf-workspace:local -m unittest discover -s /tests -v 2>&1 | tee test-artifacts/container-tests.log
python3 scripts/check-container.py 2>&1 | tee test-artifacts/container-lifecycle.log
```

Expect `Ran 39 tests`, `OK`, and `Container checks passed`. The lifecycle check uses a separate disposable archive, verifies real TCP bidirectional measurements, remote output, JSON/XML exports, saved presets, container replacement, and shutdown during a test. It cleans up only its own test containers and volume. The persistent app can stay running while these checks run, but wait until they finish before measuring network performance.

These checks establish application behavior using loopback traffic. The [README testing instructions](../README.md#test-on-the-ubuntu-server) include build logging and the recorded GitHub verification results. A completed read-only Pi inventory verified `J-RPI5`, `aarch64`, Docker daemon `26.1.5+dfsg1` on `linux/arm64`, and Compose `2.26.1-4`. Application transfer, native image build, smoke testing, and deployment did not start. The current user instruction is documentation only, and the function-change list is pending. See [Project state](project-state.md) for the desktop source and verification boundaries; passing GitHub checks do not establish a Pi deployment.

## Run a client test from Ryan's app

Start iPerf3 on the other machine. For example, with iPerf3 already installed:

```sh
iperf3 -s -p 5201
```

Leave it running during testing, then stop it with Ctrl+C. That machine must accept the connection on port 5201. UDP tests also need UDP traffic to that port; their control connection uses TCP.

In Ryan's web interface:

1. Choose **Client · test a remote server** and enter the other machine's address, such as `192.168.1.200`, with port `5201`.
2. Start with TCP, 10 seconds, and one stream. Leave **Local bind address** blank.
3. Choose Upload, Download, or Bidirectional. Upload sends from the app's client to the target; Download sends from the target to that client. Bidirectional runs both together. These directions follow [iPerf3's client perspective](https://software.es.net/iperf/invoking.html).
4. Give the run a label and record the link, location, connection type, and conditions in Notes. Select **Start session**.
5. Check the live graph and log, then open the completed run from **Previous tests**. Its row shows interval maximum upload/download, averages, measured runtime, and a throughput trend.

Only one session runs at a time in an app instance. For UDP, choose a deliberate bandwidth target such as `10M`; it is applied per stream. Preserve that target and stream count when comparing runs. Windows builds disable UDP bidirectional; the Ubuntu container supports it when the peer supports it.

## Accept a test on Ryan's end

Use this when another machine should connect to Ryan's app as the iPerf server. Publishing port 5201 alone does not start a server; Ryan must start a server session in the interface.

In the repository's `.env` file, set the following values, replacing the example address with an IPv4 address assigned to Ryan's Docker host. Merge these settings into an existing file:

```dotenv
IPERF_BIND_ADDRESS=192.168.1.100
IPERF_PORT=5201
```

Apply the mapping:

```sh
docker compose up -d --no-build --wait
docker compose port app 5201
```

Compose publishes TCP and UDP on that host address. Keep the web port on loopback. Confirm that the network path permits incoming iPerf traffic from the test client. Docker's published ports require appropriate host/network controls; [Docker documents their interaction with Ubuntu firewalls](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations).

In the web interface, choose **Server · accept one test**, set port `5201`, leave **Local bind address** blank, label the session, and select **Start session**. The server accepts one test and then exits. Start another server session before each subsequent test. Waiting for a client defaults to 300 seconds and produces no throughput result if it times out.

From the other machine, run one of these commands while Ryan's server session is waiting:

```sh
# Upload from this client to Ryan; save a native JSON result.
iperf3 -c 192.168.1.100 -p 5201 -t 10 -J --get-server-output > client-upload.json

# Start another server session in Ryan's app before this download test.
iperf3 -c 192.168.1.100 -p 5201 -t 10 -R -J --get-server-output > client-download.json
```

Ryan's app archives its server measurements and logs. The client files preserve the client's measurements and returned server output. Import those files using **Import saved tests** if both records are needed in the app. Native JSON contains the data supplied by iPerf; full app exports also contain the app's event history and notes.

If host port 5201 is occupied, set `IPERF_PORT=5202` and recreate the app. The other machine connects to **5202**, while the port inside Ryan's app stays **5201**. Do not run a separate host `iperf3 -s` on the same published host address and port.

## Inspect remote results and compare a fix

For a completed client session, use **Chart results source** to select **Remote server results** or **Compare local & remote**. Returned server curves are dashed, and **Remote server raw output** retains what the peer supplied. The app requests that output automatically. iPerf supplies server output after a completed test; cancelled tests and some public servers may have no usable remote measurements. Local server sessions already display the server's own measurements.

To document a fix:

1. Run a baseline before making the change. Record the client and server locations, wired/Wi-Fi connection, protocol, direction, duration, streams, UDP target if used, and relevant background traffic in Notes.
2. Export the session using **JSON** or **XML**, then **Export session**. Both formats retain the same available measurements and complete captured app logs.
3. Apply the change and repeat with the same configuration. Use **Load settings** on the baseline to fill the form, then change the label and notes before starting.
4. Under **Compare tests**, select the baseline and follow-up. Review throughput, retransmissions, jitter, and packet loss where reported, plus any warnings about different settings.
5. Choose the export format and select **Export comparison**. It includes both source runs and their calculated differences.

Upload is blue and download is green. Missing measurements are blank; a blank is not zero. A row's maximum is a recorded interval peak, while its average is a final reported rate. Sparkline scales vary by run, so compare the numbers and full graph rather than sparkline height alone. Keep container networking and test conditions consistent; one higher peak does not establish a sustained improvement.

## Share results and server presets

Send Ryan the full session or comparison JSON/XML exports through your usual file-sharing method. He can open **Previous tests → Import saved tests** and select the files. Imported records get new local IDs and retain supplied settings, results, notes, dates, logs, and provenance. Importing equivalent JSON/XML objects again skips duplicates of earlier imports. Files are limited to 20 MiB each. A bare `run.json` metadata file lacks the separate log file; use full exports when sharing evidence.

Server presets are separate from test evidence. In **Server presets**, use **Export presets JSON** and **Import server JSON** to share them. Public listings can be loaded and edited; loading does not start a test. Use **Save the current test settings as a custom preset** for a private endpoint.

The [example Ryan server preset](examples/ryan-server-presets.json) contains TCP upload defaults for `192.168.1.100:5201`. Replace the address and any port mapping before importing it. Its endpoint must have a waiting server session or a running standalone iPerf server. Imported preset files merge into the catalog and do not delete saved tests.

## Keep and back up the archive

The app saves automatically under `/data/runs` in the Compose named volume. JSON/XML export is an additional portable copy. Stop, restart, and rebuild preserve the volume. Keep the same checkout directory and Compose project name when updating; changing the project name can select a different volume and make history appear empty. `docker compose down --volumes` deletes the archive.

For a complete backup, run these commands on the host while no test is active:

```bash
set -euo pipefail
backup_dir="$HOME/iperf-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
docker compose stop app
container_id=$(docker compose ps --all --quiet app)
docker cp "$container_id:/data/runs" "$backup_dir/"
git rev-parse HEAD > "$backup_dir/tested-commit.txt"
docker compose up -d --no-build --wait
printf 'Backup saved to %s\n' "$backup_dir"
```

This copies run metadata, event logs, and any saved preset catalog outside the container and repository. Copy the backup to another machine for recovery. Restore portable session/preset exports through the interface, or follow the [fresh archive migration procedure](../README.md#ubuntu-container) to preserve original run IDs. Do not copy a backup over an existing archive containing evidence.

Before updating, back up the archive, then run:

```sh
git pull --ff-only
docker compose up -d --build --wait
docker compose ps
```

Confirm that an older run still opens with its notes and logs after the update.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| SSH reports permission denied | Use the correct host account and authorized SSH key. The app does not create SSH access. |
| Docker reports permission denied | Confirm that the host account has Docker access; follow Docker's installation/post-install instructions. |
| Web page is unreachable | Check `docker compose ps`, readiness, and the SSH tunnel. The default mapping requests loopback; see the pre-28 Docker limitation above. |
| Container is unhealthy | Read `docker compose logs --tail=200 app`; check `/api/ready`, free disk space, and archive permissions. `/api/health` checks the unused starter database and is not the container readiness probe. |
| Server test cannot connect | Check the address, published host port, TCP/UDP reachability, and that the one-test server session is waiting. |
| Port is already allocated | Check for another listener. Set `WEB_PORT` for the host web port or `IPERF_PORT` for the host iPerf port, then recreate the container and use the new port. |
| Another process owns the archive | Keep one app process per archive. Do not mount the same archive into two running app instances. |
| Remote graph is empty | Finish a client test and inspect its raw server output. The peer may omit output or usable measurements. |
| History appears empty after an update | Check the Compose project name and volume before running new tests or restoring data. |

For help, share the tested Git commit, host architecture, `docker compose ps`, relevant app/test logs, and a full export of the affected run. Review logs and notes for private addresses or other sensitive details before sharing them outside the team.

## Updated workspace navigation

Tests, final result cards, logs, exports, saved-session notes, and custom preset editing live on **Test & export**. Use **History & import** to load saved JSON/XML evidence, then Open a row for its detail. **Compare tests** overlays two or three completed runs, with one color per test and solid upload / dashed download lines; it also offers the remote-report view when available. Exporting retains every selected run and its logs.

The optional route checkbox captures at most 16 hops / 20 seconds after successful client throughput measurement. Raw route output and RTTs are retained in session exports. Clear it for throughput-only tests. Incoming server sessions skip it. Missing replies do not establish packet loss.

The Ubuntu image is intended for native AMD64 and ARM64 operation. The workflow builds/tests each architecture independently and packages the matching archive. An ARM64 artifact from CI still needs target-host verification before treating jRPI-5 deployment as complete. See [requirements](requirements.md) and [project state](project-state.md).
