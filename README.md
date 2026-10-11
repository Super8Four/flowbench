# iPerf workspace

An AdminLTE 4 web interface for iPerf3, backed by Python and Flask. Run TCP/UDP client tests or a one-test server session, watch live output and throughput, and keep an archive for later verification. AdminLTE and Bootstrap assets are bundled locally. See `THIRD_PARTY_NOTICES.md` for versions and licenses.

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
6. Choose a baseline and a follow-up under Before & after. The comparison calculates changes in throughput, retransmits, jitter, and loss when iPerf reports them. Differences in settings are flagged. Exporting a comparison includes both original runs, their logs, and the calculated changes.

The Windows build was verified for TCP upload, download, bidirectional, parallel streams, UDP upload/download, and one-test server sessions. UDP bidirectional is disabled on Windows because the tested Cygwin build stalls in that mode. Platforms that do not report TCP retransmissions display no value for that metric; absent metrics are not turned into zeroes. Older builds without JSON streaming use live verbose text output; their export still retains all raw output and parsed throughput.

Server mode listens on the selected port and optional local bind address, accepts one test, then exits. The peer's actual protocol, duration, streams, and direction are recorded from iPerf output. Waiting for a client is bounded by the configured idle timeout. An idle session has no performance metrics to compare.

## Persistent storage and exports

The authoritative archive is **`instance/runs/`**, independent of browser storage and the starter PostgreSQL connection. Each run has its own UUID directory containing:

- `run.json`: settings, notes, timestamps in UTC, status, exit code, interval measurements, iPerf version, command arguments, and native results.
- `events.jsonl`: an append-only log of timestamped stdout, stderr, and app events, with sequence numbers for replay.

Output is written before it is displayed. Metadata is replaced atomically. Completed, failed, cancelled, and interrupted sessions remain available after an app restart. If the app stops abruptly, unfinished records are marked interrupted on the next start. Closing the browser leaves the active test running. The log captures what the launched process emits, including remote output requested with `--get-server-output`; it is not packet capture or a collector for unrelated external iPerf processes. Enable debug output for additional iPerf diagnostics.

Set `RUNS_DIRECTORY` to change the archive location. Relative paths resolve from the app's working directory. Back up the **whole archive directory**, preferably with the app stopped. No automatic deletion or retention limit is applied. Browser local storage holds only selected comparison IDs; it does not hold measurement data.

JSON and XML exports include the same settings, metadata, metrics, intervals, native results, raw stdout/stderr, and complete event history. Exporting a running session produces a snapshot of the data captured so far. XML uses typed `field` and `item` elements to preserve nested objects, arrays, numbers, booleans, and nulls. Strings with characters XML cannot represent use base64 with an explicit encoding attribute.

The existing PostgreSQL configuration, `Measurement` starter model, `init-db` command, and database-only Compose service are retained for future database integration. Test history currently uses the disk archive; it does not require PostgreSQL or a schema migration. `/api/health` reports the starter database's connection status; `/api/status` reports the iPerf engine, archive path, and active session.

## Docker migration (next step)

The app is currently run and verified locally. App containerization has not been added yet. For migration, install iPerf3 inside the Linux container, run one Waitress process with threads, set `RUNS_DIRECTORY=/data/runs`, and mount a persistent volume at `/data`. Copy the existing archive into that volume so baseline evidence survives the move and container replacements. Preserve the volume in backups. The existing `compose.yaml` starts only PostgreSQL.

The local control API has no authentication. Keep the current loopback binding for this phase; access control belongs in the deployment setup if the UI is made available to other machines.

## Checks and API

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check app\static\app.js
```

Tests use a temporary archive and real iPerf3 loopback processes. They cover live output, TCP directions and parallel streams, UDP metrics, server sessions, cancellation, connection failures, input validation, archive recovery/reopening, comparison calculations, and equivalent JSON/XML exports. Integration checks skip when iPerf3 is not installed.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/status` | Engine capabilities, storage location, active session |
| `GET /api/runs` | Saved history and active run ID |
| `POST /api/runs` | Start a client or one-test server session from JSON settings |
| `GET /api/runs/<id>?after=<sequence>` | Metrics and output after a saved event cursor |
| `POST /api/runs/<id>/stop` | Stop the active session; send `{}` as JSON |
| `PATCH /api/runs/<id>` | Update `label` and/or `notes` |
| `GET /api/runs/<id>/export?format=json\|xml` | Download a complete run |
| `GET /api/comparison?before=<id>&after=<id>` | Compare two completed tests |
| `GET /api/comparison?before=<id>&after=<id>&format=json\|xml` | Export comparison and both source runs |

Control requests use JSON, reject cross-origin browser writes, and execute validated argument arrays without a shell. One session runs at a time to avoid overlapping tests distorting measurements.
