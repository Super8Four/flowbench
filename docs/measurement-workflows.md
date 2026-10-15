# Measurement workflows

These workflows keep a change, its traffic measurements, local diagnostic evidence and observations together in the SQLite archive. They do not enable authentication, remote monitoring access or automated deployment. Keep the app on loopback or a trusted network as described in the [installation guide](install/README.md).

## Projects and baselines

Create a named project for a change such as a circuit upgrade or switch replacement. Assign existing tests or select the project when starting new tests. A project can use one completed individual test with measured results as its baseline. For a Both or matrix job, select a measured child rather than the parent: the parent groups tests and has no combined throughput measurement.

The project lists measured changes against that baseline and identifies differences in test conditions. A higher throughput value or lower retransmission count describes an observed change, not proof that the change caused it. Different protocols, directions, targets, ports, stream counts, durations, address families, UDP rates or engine versions need separate interpretation. Repeat tests under comparable conditions and retain notes about other traffic. Failed or unfinished tests remain evidence but are excluded from a completed before/after summary.

Clear or change a project's baseline before moving that test to another project. An imported project receives a new local ID; its run IDs, parent/child links and baseline reference are remapped together. The import validates the complete bundle before writing and commits it atomically. Reimporting the identical JSON or typed XML bundle skips the duplicate project.

## Ping, local health and path evidence

Diagnostics are opt-in for an outgoing test and apply only to its selected peer. The app runs bounded local OS tools as argument arrays without a shell. It does not scan neighboring hosts, install tools on demand, raise process privileges, add capabilities, configure credentials or change firewall rules.

Latency uses Windows `ping` or an available Linux `ping`. It captures up to three baseline echo probes before traffic, then probes during traffic at approximately one-second intervals, up to 120 loaded samples. A probe has a bounded wait; summaries include reply/missing counts and numerical RTT variability where parsing produced actual measurements. A result such as `<1 ms` is stored as an upper bound, not an invented zero. Tool absence, blocked permissions, timeout, missing replies and cancellation are recorded distinctly. Raw evidence is retained when an output format cannot be parsed.

ICMP echo RTT is not application latency. Other traffic may already exist during the baseline, ICMP can receive different treatment than iPerf traffic, and short tests may produce few loaded samples. A missing response is not proof that application packets were lost. Long tests continue their traffic after the diagnostic sample limit; the evidence states when that limit was reached.

Local endpoint health uses Linux `/proc` CPU/interface counters or fixed, read-only Windows CIM and adapter-statistics queries when available. Linux CPU utilization is derived from counter changes across the test; Windows CPU load is an OS snapshot. Interface counters belong to the local machine or container network namespace and can include unrelated traffic. Interfaces with reset or missing counters do not receive invented deltas. A plain iPerf peer provides no remote CPU or interface counters: remote health is explicitly unavailable and no remote session is attempted.

Each new outgoing test receives one route capture before traffic, using Windows `tracert` or an available Linux `tracepath`, with numeric addresses, at most 16 hops and a 20-second overall limit. Both/matrix children and Flent receive their own preparation. Ordered hops retain missing replies and bounded RTTs; the route does not prove a failing link or a bottleneck and can differ from the traffic path. A separate read-only local clock-quality observation records UTC provenance and available daemon status without claiming independently verified NTP time. See [route and clock preparation](preflight.md) for cancellation, passive incoming capture and historical-record limits.

The optional MTU check tries fragmentation-disabled echo packets of 1500, 1400 and 1280 bytes against the same selected peer, stopping after a confirmed response. It is bounded to roughly 4.5 seconds, with bounded individual probes. The largest confirmed tested packet is evidence, not complete path MTU discovery. Missing replies can mean filtering or permission restrictions. IPv6 support depends on the native tool; unsupported combinations are marked unavailable.

The shipped container runs with dropped capabilities and a read-only root. Diagnostic availability depends on its OS tools, network namespace and permissions; traffic can complete while a diagnostic is unavailable. Do not grant privileges merely to make a diagnostic badge green.

## Controlled matrices

A matrix expands the selected TCP/UDP protocols, upload/download/bidirectional directions and stream counts into individual tests. Children run sequentially under one reserved outgoing job. The app preserves each child's original intervals, native results, diagnostics and logs, rather than averaging different settings into one result.

Limits are 12 children, 1–8 streams per child, 60 seconds per child and 360 seconds of planned traffic time. UDP must have a finite positive rate and an aggregate requested rate of at most 100 Mbit/s across streams and directions. The rate entered in iPerf is per stream; bidirectional UDP also sends in both directions. TCP still seeks available throughput; planned duration and stream limits do not impose a TCP bandwidth cap. Probe and connection overhead can extend wall-clock runtime beyond planned traffic time.

The app validates the whole plan and native engine support before writing a job or starting traffic. The Windows bundled engine does not support bidirectional UDP. Stop cancels the current child and records later unstarted children as skipped. Restart recovery preserves committed evidence and marks unfinished records interrupted; it does not automatically resume matrix traffic. A managed incoming listener remains independent and can overlap an outgoing child, so retain the recorded overlap caveat when interpreting results.

## Live comparison and annotations

Saved comparisons accept additional completed individual tests through **Add test**, with a saved history-marker color picker for each run. The original three-run cap is removed. Each plotted iPerf source/direction has a separate comparison color, with numbered labels and line patterns. Colors stay stable for the same selection across reorder, polling and visibility changes. Adding/removing tests can reassign comparison colors; saved run colors remain unchanged. Large selections initially show a subset of traces that can be enabled in the legend, while retaining all selected rows and evidence. API responses and prepared imports have a 32 MiB data budget; uploads remain limited to 20 MiB files in 32 MiB requests. Resource errors ask for smaller groups rather than discarding data silently.

Different durations share an elapsed-time axis. A ten-second trace ends at its last captured interval when displayed beside a thirty-second trace. Whole-run summary values still describe each complete test. The common window ends at the shortest measured series; a separate duration-weighted window mean is available only from exact interval bins with complete coverage. Missing, overlapping, end-only or cut intervals leave that mean unavailable.

Rates display decimal SI units (`bps`, `kbps`, `Mbps`, `Gbps`, `Tbps`), with a common axis unit across compared curves. These are bits per second, distinct from byte counts. Native `bits_per_second` and derived `*_mbps` measurements remain numeric and unchanged in SQLite, JSON and XML; the exports include their unit contract. Color is optional presentation metadata, separate from test settings used for comparability.

Choose a completed measured individual baseline to overlay against an active individual test. Elapsed seconds begin at each test's traffic start; wall-clock timestamps are not aligned. Only measured intervals appear, and missing spans remain gaps. The overlay flags mismatched settings and keeps ICMP timing separate from throughput. Selecting a Both or matrix parent does not create a combined baseline; select its child.

Run labels, notes and the site/device/circuit/ticket tags are editable. Timeline notes have a timezone-aware timestamp and an optional elapsed second. They are user observations, separate from original native measurement output. Editing a parent job's project or tags also updates its children transactionally; timeline notes apply to the selected record. Metadata edits publish only after storage commits.

## Endpoint detail in saved results

Current main includes a **Test endpoints** panel and compact history availability summary. Existing rc.2 downloads do not contain this addition. Selected details distinguish a captured execution/container hostname, per-reporter numeric socket pairs or typed destinations, and any captured initiating web peer. Current viewing context is separate. Both/matrix parents do not invent combined endpoints; inspect each child.

Imported and older records can show a view-only fallback from retained structured native output, with unknown hostname/initiator values left unavailable. Opening history does not send lookups or rewrite the archive. Full JSON/XML/project exports can contain sensitive endpoint/context values; PDF/Word default redaction removes them, including raw and nested copies. See [endpoint provenance and sharing](test-endpoints.md).

## SQLite, export and compatibility

The database remains `RUNS_DIRECTORY/archive.sqlite3`, or `/data/runs/archive.sqlite3` in the persistent container volume. Runs are JSON documents in SQLite's `runs` table, raw events are separate ordered records, and project catalog metadata is stored in the same database. Diagnostic data, route evidence, tags and timeline notes commit with their run documents. These are actual database writes; no database server is required.

Run, job and project exports use versioned JSON or the app's typed XML format. Both formats retain nested evidence, unavailable statuses, timestamps and native results. Imported files create new local identities without launching traffic. A standalone run or job import keeps its original project identity as provenance and leaves local project membership empty; import the complete project bundle to restore its catalog and baseline. A separately imported child likewise keeps its original parent identity as provenance. Old saved runs remain readable without fabricated diagnostic fields or annotations. Preserve a complete archive backup before upgrades; use one app process with multiple Waitress threads and a writable local data volume. See [storage migration and rollback](sqlite-storage.md) and [maintenance](install/maintenance.md).

A legacy snapshot can preserve additive records and `projects.json` for a compatible newer build. Older releases can ignore unfamiliar fields and cannot offer these new workflows; rollback is not a claim of feature compatibility. Keep the complete SQLite backup and the exact newer source/image identity. Never overwrite an existing archive to test an import or rollback.

## Standalone checks and repeated executions

Use **Test & export → Measurement type** for a standalone DNS lookup or ICMP echo check. These save typed attempts and outcomes without throughput values, and can be filtered in history, compared with the same check type and included in PDF/Word reports. A failed or partial check can still contain useful evidence; missing sends, replies and RTTs remain unavailable. See the [check workflows and interpretation limits](network-checks.md).

**Run again** starts a new execution from saved configuration, with a new identity and immutable source link. Ordinary outgoing iPerf, Both/matrix and DNS/ICMP records start immediately after validation; Flent and incoming records first require their review panel and a fresh confirmation. **Load settings** fills the editable form for review or changes before a normal new start and does not promise lineage. Neither action rewrites the saved source; opening, exporting or importing evidence starts no traffic.

## Verification boundaries

The workflow tests use disposable SQLite archives and synthetic records to check direct database contents, reopen persistence, JSON/XML interchange, project ID remapping, failed-import rollback, legacy compatibility and sparse live comparisons. Container lifecycle checks use bounded, disposable loopback iPerf peers and a fresh volume, verify native diagnostic status honestly under dropped capabilities, then replace the container and compare saved exports.

Passing those checks does not establish a physical Pi deployment, external network performance, remote health access, full accessibility acceptance or resilience to physical storage failure. Consult the CI result for the exact source commit; existing prerelease tags and assets remain immutable.

### Throughput series colors

Session activity uses green upload and yellow download, with distinct local and remote shades. Upload means client to server; download means server to client, including remote server reports. Local upload is solid, local download dashed, remote upload dotted and remote download dash-dot. Legends and point tooltips name the report source and client-perspective direction. Missing remote measurements produce no invented trace or legend entry.

Comparison colors come from the complete selected set before hiding traces or choosing local/remote reports. The palette extends beyond eight colors without cycling; large selections still need labels, patterns and visibility controls because nearby colors become harder to distinguish. The saved color picker changes the run marker in history, not the generated comparison-series colors. Actual elapsed durations, gaps, captured samples and automatic SI units are preserved. These are display changes and require no SQLite migration. Existing JSON/XML files and backups retain saved run colors.
