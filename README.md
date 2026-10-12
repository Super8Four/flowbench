# FlowBench

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=feature%2Fsqlite-storage)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Afeature%2Fsqlite-storage) [![Prerelease](docs/assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) [![Native images](docs/assets/badges/platform.svg)](docs/setup-and-testing.md) [![Storage](docs/assets/badges/storage.svg)](docs/sqlite-storage.md)

**Measure. Compare. Verify.**

A portable iPerf3 workspace for TCP/UDP tests, incoming capture, saved evidence and comparisons. FlowBench uses Flask, locally bundled AdminLTE/Bootstrap, and SQLite. The GitHub repository is now **Super8Four/flowbench**; existing image tags and archive paths remain compatible.

![FlowBench test and export page](docs/assets/test-desktop.jpg)

*Actual UI with synthetic demonstration records; these values are not deployment measurements. [More screenshots and verification](docs/verification.md).*

## Start on Ubuntu

Use a Docker Engine host with Compose v2. From this checkout:

```sh
docker compose config --quiet
docker compose build
docker compose up -d --wait
curl --fail http://127.0.0.1:5000/api/ready
```

Open **http://127.0.0.1:5000**. The image uses Ubuntu 24.04, Python 3.12 and checksummed upstream iPerf3 3.22. Native AMD64 and ARM64 CI runs the application and container lifecycle tests. ARM64 images are suitable for evaluation on an Ubuntu ARM64 host; no Raspberry Pi deployment or device performance acceptance is claimed.

The first release is **0.1.0-rc.1**, a prerelease. The [release](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) includes source downloads and separately verified native image archives, image metadata and SHA-256 checksums. Load only the matching architecture; see [installation](docs/setup-and-testing.md). No registry image is published by this workflow.

## Measure and keep evidence

1. Start an iPerf3 peer on an authorized target (`iperf3 -s`). Tests originate from the FlowBench host.
2. In **Test & export**, select TCP, UDP or **Both (TCP then UDP)**. New tests default to bidirectional. Explicit saved choices are retained. Both saves separate protocol results and an aggregate job; it does not combine TCP/UDP rates.
3. Start the test. View measured throughput, timestamped logs, local/remote reports and optional route evidence. Missing measurements stay unavailable. Stop preserves captured partial evidence.
4. Use **History & import** for search, labels/notes, JSON/XML export and additive import. **Compare tests** overlays two or three completed measurements and warns about settings differences.

In **Configuration** or **Incoming capture**, explicitly enable a managed listener. Ordinary iPerf3 clients are captured automatically without a browser or API-created run. The listener uses a separate process, accepts one incoming test at a time, and can coexist with one outgoing test/job. Shared host/network resources can affect both measurements; overlapping runs are labeled. Saved enable settings resume after app restart; Stop disables them. See [listener operation](docs/incoming-listener.md).

The archive is `/data/runs/archive.sqlite3` in the persistent Compose `iperf_data` volume. It contains runs, events, notes, custom presets and listener settings. Main previously used JSON/JSONL files; PostgreSQL was unused starter scaffold. Upgrade imports those files transactionally and leaves them intact. No live PostgreSQL connection or data modification occurs. [Migration, backup and rollback](docs/sqlite-storage.md) explains safe recovery.

## Access and limits

The web API has **no authentication**. Compose publishes to host loopback by default. Keep it local or behind an approved authenticated access path; the application does not configure firewalls, TLS or credentials. The listener defaults to loopback **inside** the app/container. Reaching it through a container mapping requires an explicitly chosen container bind and host mapping. No deployment or access expansion is performed by these instructions.

The pinned Windows engine cannot execute UDP bidirectional reliably; FlowBench rejects UDP/Both bidirectional on Windows before starting. Use the Linux image, or explicitly choose upload/download. Public preset entries are a bundled reference catalog; availability and permission to test them are not guaranteed.

## Development and documentation

For Windows, use Python 3.12 or newer, create `.venv`, install `requirements.txt`, run `scripts/install-iperf.ps1` to obtain the checksummed engine, then `scripts/start.ps1`. Set `IPERF_PATH` and `RUNS_DIRECTORY` in `.env` as needed. One Waitress process owns an archive; use its threads rather than multiple workers or the Flask reloader.

- [Installation, backups and troubleshooting](docs/setup-and-testing.md)
- [Incoming listener and API](docs/incoming-listener.md)
- [TCP, UDP, Both and direction semantics](docs/protocol-tests.md)
- [SQLite migration and rollback](docs/sqlite-storage.md)
- [Requirements and remaining gaps](docs/requirements.md)
- [Project state](docs/project-state.md), [verification](docs/verification.md), [changelog](CHANGELOG.md) and [release notes](docs/release-0.1.0-rc.1.md)

The requested upstream UI/UX skill is vendored under `.agents/skills/ui-ux-pro-max`, pinned to revision `7246805992a42b7bba37d4cac83b01416f0c6c44`; see [AGENTS.md](AGENTS.md) and [third-party notices](THIRD_PARTY_NOTICES.md). It is development guidance, not a runtime dependency. No application license badge is shown because the repository has no declared application license.
