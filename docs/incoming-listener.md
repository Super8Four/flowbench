# FlowBench managed incoming listener

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

Enable the listener explicitly on **Configuration** or **Incoming capture**. The default numeric bind is `127.0.0.1`, port `5201`, maximum requested duration `3600` seconds. Opening a page starts no traffic. Listener settings and enabled state are saved in SQLite; an enabled listener resumes on app restart. **Stop listener** stops its process and disables future resume. If the disabled setting cannot be saved, runtime still stops and the UI/API reports the failure; the saved enabled state remains until repaired.

The listener needs the bundled iPerf3 3.22 capabilities: streaming JSON, full JSON and server duration limits. It is persistent, with a 30-second control idle timeout and 15-second receive timeout. Longer/unlimited requested durations are rejected by the engine. It accepts **one incoming test at a time**; additional clients get engine busy/reset errors, not fabricated archived tests. Restart after an unexpected process/bind failure is explicit while the app is running. Saved enable state is retained for the next app startup.

Example ordinary clients against an authorized peer (replace the reserved example address):

```sh
iperf3 -c 192.0.2.20 -p 5201 -t 10 -b 2M -J
iperf3 -c 192.0.2.20 -p 5201 -t 10 -b 2M -R -J
iperf3 -c 192.0.2.20 -p 5201 -t 10 -b 2M --bidir -J
iperf3 -c 192.0.2.20 -p 5201 -t 10 -b 2M -u -J
```

These clients need no FlowBench API/browser action. Each accepted session receives its own UUID, dates, actual remote control endpoint, client-supplied protocol/direction/streams/duration, listener generation and original start/cookie evidence. Server directions use the **connecting client's perspective**: upload enters this server; download leaves it. A normal streamed end and subsequent full JSON belong to the same session, not two captures.

SQLite commits streamed measured state with each event. The merged listener stdout/stderr stream is explicitly identified as combined; the process does not expose separate per-session exit codes. Native JSON and partial logs are retained. Rejected connections with identifiable accepted-peer evidence can be saved as failed sessions; idle/bind diagnostics without an accepted peer do not create fake runs. Exact native parameters are preserved; UI rerun fields stay within normal limits. JSON/XML exports and imports retain incoming metadata and original evidence.

One outgoing client/job can run concurrently in its own process. Both protocol children remain sequential. Overlapping incoming/outbound records retain mutual run IDs and a measurement-interference note. Rates are never combined. FlowBench does not promise unaffected measurements or unlimited concurrent clients. A legacy one-test server cannot share the managed listener port.

Stopping during a session preserves a `cancelled` partial run. Unexpected listener death preserves `interrupted` evidence; storage capture failures surface as `failed` and disable readiness. Startup recovery marks unfinished records interrupted and never replays completed sessions. Graceful app shutdown stops active incoming/outgoing traffic and retains saved enable state for later resume. Abrupt power loss and hardware durability remain separate operational concerns.

## API and maintenance

| Route | Method | Result |
| --- | --- | --- |
| `/api/listener` | GET | State, settings, active run ID, diagnostics and one-session limit |
| `/api/listener` | POST JSON | Enable `{bind, port, max_duration, label, notes}`; validation 400, conflict 409, storage error 503 |
| `/api/listener/stop` | POST JSON `{}` | Stop and save disabled state |

Cross-origin browser writes and non-JSON control requests are rejected. This is not authentication. Keep the web API within an approved access boundary. Captures only cover this managed iPerf process, not unrelated local servers.

Set `FLOWBENCH_RESUME_LISTENER=0` before maintenance CLI operations/exports to prevent saved listener startup. This suppresses startup for that process without altering configuration. Full backups include the listener metadata; legacy rollback exports do not provide automatic listener resume in the old application.

This feature does **not** implement R17 authenticated FlowBench-to-FlowBench coordination, peer authorization, instance pairing or cross-instance correlation. Those remain pending.
