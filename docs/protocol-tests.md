# FlowBench TCP, UDP and Both tests

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

New client tests default to **Bidirectional**. An explicit upload/download/bidirectional selection in a saved preset, run or import is retained. Legacy imports/preset catalogs that omit direction keep their earlier upload default. Loading or saving settings never starts traffic.

Choose TCP, UDP or **Both (TCP then UDP)**. Both is one job containing two independently saved protocol runs, TCP first and UDP second, each using the selected duration, streams, direction and endpoint. They never overlap. Keep the peer listening for two connections, for example with `iperf3 -s`; `iperf3 -s -1` accepts only the first test. FlowBench-to-FlowBench coordination is not implemented by this feature. The managed incoming listener (and legacy one-test server API) follows its connecting client's actual protocol/direction and does not accept a Both client-job setting.

UDP uses the selected bandwidth per stream; its default remains `10M`. Validation tests use bounded `2M`/`5M` traffic on localhost. Changing direction or protocol does not silently change a saved choice. The pinned Windows iPerf3 build cannot run UDP bidirectional: UDP/Both bidirectional requests are rejected before any test/artifact starts, with an explicit explanation. Use a supported Linux container for that mode, or explicitly select upload/download on Windows. Unsupported `--bidir` builds also reject bidirectional before starting.

Both reserves the existing single-session slot throughout both protocols. Stop terminates the active test and skips any unstarted protocol; shutdown uses the same cancellation path. A protocol execution failure permits the other protocol to run, unless cancellation/storage failure prevents it. A restart marks unfinished jobs/queued tests interrupted and never resumes traffic automatically.

Job outcomes are:

- `completed`: both protocols completed.
- `partial`: exactly one completed; inspect the other protocol's failure/skipped evidence.
- `failed`: neither completed, or the job's final archive write failed.
- `cancelled`: an explicit stop/shutdown occurred, including after one completed protocol.
- `interrupted`: startup recovered an unfinished job.

Each child retains its own status, settings, measured directions, dates, logs, local/remote results, route evidence and notes. A skipped test has no measurements. The job has no combined throughput/chart/runtime: TCP and UDP measurements remain separate. Open the job to inspect its result table, then Open TCP/UDP for its charts, logs and remote report. History includes the job and the two protocol-labeled rows. Completed children can be compared normally; the job is excluded because it has no performance measurements.

SQLite stores the normal schema-version-1 run documents plus additive `kind: protocol-job`, parent `children` references and child `protocol_job` identity/order metadata. Job creation writes all three records atomically. HTTP status/history expose the active parent job identity; Stop accepts that identity or either child while the job is active.

Exporting a child produces an ordinary compatible JSON/XML run. Exporting the parent produces a `protocol-job` envelope containing both complete child exports. Import validates identities/protocol order/outcomes before one transaction saves the parent, children and events. Local IDs are remapped with source provenance retained. Reimporting an identical JSON/XML job skips the whole group; snapshots become interrupted. A whole-job import owns its own child copies, even if one child was previously imported individually.

The [SQLite migration/rollback guide](sqlite-storage.md) still applies. Legacy file snapshots retain group/child identities for this version. Releases predating Both do not understand its parent envelope/statuses or execution setting; use individual TCP/UDP exports for interchange with those releases, and retain the newer SQLite backup for complete job recovery.

Automated tests cover new defaults, explicit/legacy direction choices, bounded TCP/UDP/Both loopback, native Linux bidirectional measurements, mocked bidirectional JSON parsing/local/remote results, atomic creation/import failure, first/second-test cancellation, partial/failure outcomes, skip/restart behavior, preset persistence, JSON/XML deduplication, remapped identity and legacy snapshot recovery. Native AMD64/ARM64 lifecycle CI additionally checks real Both bidirectional measurements, group interchange and persistence through container replacement. Windows tests verify the explicit UDP-bidirectional restriction; they do not claim Windows executed that unsupported mode.

See [managed incoming capture](incoming-listener.md) for persistent listener settings, ordinary-client evidence, independent outgoing execution and interference labels.
