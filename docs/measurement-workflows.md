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

Route capture uses Windows `tracert` or an available Linux `tracepath`, with numeric addresses, at most 16 hops and a 20-second overall limit. It runs after traffic and preserves structured hops, status and bounded raw output. Missing hop responses do not prove a failing link, and the diagnostic route can differ from the path taken by traffic. Cancellation terminates the owned probe process.

The optional MTU check tries fragmentation-disabled echo packets of 1500, 1400 and 1280 bytes against the same selected peer, stopping after a confirmed response. It is bounded to roughly 4.5 seconds, with bounded individual probes. The largest confirmed tested packet is evidence, not complete path MTU discovery. Missing replies can mean filtering or permission restrictions. IPv6 support depends on the native tool; unsupported combinations are marked unavailable.

The shipped container runs with dropped capabilities and a read-only root. Diagnostic availability depends on its OS tools, network namespace and permissions; traffic can complete while a diagnostic is unavailable. Do not grant privileges merely to make a diagnostic badge green.

## Controlled matrices

A matrix expands the selected TCP/UDP protocols, upload/download/bidirectional directions and stream counts into individual tests. Children run sequentially under one reserved outgoing job. The app preserves each child's original intervals, native results, diagnostics and logs, rather than averaging different settings into one result.

Limits are 12 children, 1–8 streams per child, 60 seconds per child and 360 seconds of planned traffic time. UDP must have a finite positive rate and an aggregate requested rate of at most 100 Mbit/s across streams and directions. The rate entered in iPerf is per stream; bidirectional UDP also sends in both directions. TCP still seeks available throughput; planned duration and stream limits do not impose a TCP bandwidth cap. Probe and connection overhead can extend wall-clock runtime beyond planned traffic time.

The app validates the whole plan and native engine support before writing a job or starting traffic. The Windows bundled engine does not support bidirectional UDP. Stop cancels the current child and records later unstarted children as skipped. Restart recovery preserves committed evidence and marks unfinished records interrupted; it does not automatically resume matrix traffic. A managed incoming listener remains independent and can overlap an outgoing child, so retain the recorded overlap caveat when interpreting results.

## Live comparison and annotations

Choose a completed measured individual baseline to overlay against an active individual test. Elapsed seconds begin at each test's traffic start; wall-clock timestamps are not aligned. Only measured intervals appear, and missing spans remain gaps. The overlay flags mismatched settings and keeps ICMP timing separate from throughput. Selecting a Both or matrix parent does not create a combined baseline; select its child.

Run labels, notes and the site/device/circuit/ticket tags are editable. Timeline notes have a timezone-aware timestamp and an optional elapsed second. They are user observations, separate from original native measurement output. Editing a parent job's project or tags also updates its children transactionally; timeline notes apply to the selected record. Metadata edits publish only after storage commits.

## SQLite, export and compatibility

The database remains `RUNS_DIRECTORY/archive.sqlite3`, or `/data/runs/archive.sqlite3` in the persistent container volume. Runs are JSON documents in SQLite's `runs` table, raw events are separate ordered records, and project catalog metadata is stored in the same database. Diagnostic data, route evidence, tags and timeline notes commit with their run documents. These are actual database writes; no database server is required.

Run, job and project exports use versioned JSON or the app's typed XML format. Both formats retain nested evidence, unavailable statuses, timestamps and native results. Imported files create new local identities without launching traffic. A standalone run or job import keeps its original project identity as provenance and leaves local project membership empty; import the complete project bundle to restore its catalog and baseline. A separately imported child likewise keeps its original parent identity as provenance. Old saved runs remain readable without fabricated diagnostic fields or annotations. Preserve a complete archive backup before upgrades; use one app process with multiple Waitress threads and a writable local data volume. See [storage migration and rollback](sqlite-storage.md) and [maintenance](install/maintenance.md).

A legacy snapshot can preserve additive records and `projects.json` for a compatible newer build. Older releases can ignore unfamiliar fields and cannot offer these new workflows; rollback is not a claim of feature compatibility. Keep the complete SQLite backup and the exact newer source/image identity. Never overwrite an existing archive to test an import or rollback.

## Verification boundaries

The workflow tests use disposable SQLite archives and synthetic records to check direct database contents, reopen persistence, JSON/XML interchange, project ID remapping, failed-import rollback, legacy compatibility and sparse live comparisons. Container lifecycle checks use bounded, disposable loopback iPerf peers and a fresh volume, verify native diagnostic status honestly under dropped capabilities, then replace the container and compare saved exports.

Passing those checks does not establish a physical Pi deployment, external network performance, remote health access, full accessibility acceptance or resilience to physical storage failure. Consult the CI result for the exact feature-branch commit; existing prerelease tags and assets remain immutable.
