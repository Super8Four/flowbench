# DNS checks ICMP observations and run again

The `feature/network-checks-reruns` branch adds standalone DNS and ICMP checks and a way to start a new execution from saved configuration. These are development features; the published rc.1 and rc.2 downloads are unchanged. See [planned diagnostics](roadmap.md) for work that remains outside this implementation.

## DNS query

Choose the question name, record type, system or custom numeric resolver, UDP or TCP, query count and timeout. PTR accepts a numeric IP address and constructs its reverse question. The app reads configured system resolver addresses without querying the question first. The saved result records which resolver address, family and port were actually selected.

Queries are bounded to one through five logical questions, each with a timeout of 250 through 3000 milliseconds. A truncated UDP response can lead to one recorded TCP fallback for that question. Each transport attempt retains its own timing, response code, flags, question and available answer, authority and additional records, including TTL. Truncation and missing evidence remain explicit.

NXDOMAIN, NODATA and server error responses are captured observations. They are distinct from timeouts, malformed replies and unavailable resolvers. A completed DNS observation does not mean that the name resolves successfully, that every resolver agrees, or that DNSSEC was validated. Comparisons count the final attempt for each logical question. Answer-set changes exclude TTL, which can change with cache age; incomplete captures do not claim complete added or removed answer sets.

DNS work uses the pinned dnspython dependency in an owned worker. It queries only as part of a requested check. No public resolver is selected as an automatic fallback. Missing configuration, permissions or dependency support is shown as unavailable rather than replaced with invented results.

## ICMP echo nonresponse

Choose a hostname or numeric address, an IP family and one through sixty probes. A hostname is resolved once through the host resolver and the actual selected destination and candidate addresses are saved. Every probe uses that pinned destination. Numeric destinations do not require a hostname lookup.

The report distinguishes confirmed transmitted probes, echo replies, confirmed nonresponses and indeterminate attempts. A percentage requires a known send and reply denominator. Unknown transmission or reply counts leave the percentage unavailable. Numeric RTT summaries use actual numeric samples; a strict bound such as `<1 ms` remains a bound and is excluded from numeric averages. Missing replies are not zero RTT.

This is **ICMP echo nonresponse**, not application or UDP packet loss. Filtering, rate limits, routing, permissions and native output limitations can affect the observation. Directional UDP loss remains the separate [iPerf UDP measurement](protocol-tests.md), using the receiver's available native loss evidence. It requires a compatible controlled iPerf peer and does not use an ICMP percentage as a substitute.

The app invokes a bounded native ping process for each attempt. Tool presence is a capability hint, not proof that ICMP is permitted. The app does not change host permissions, container capabilities or firewall rules to obtain a measurement. Cancellation stops owned work and retains the captured subset. Interrupted checks are not automatically resumed after restart.

## Saved evidence and comparisons

Standalone records use `kind: diagnostic-check`, `check_type: dns` or `loss`, typed settings and a top-level versioned `result`. They contain no iPerf throughput measurements. Existing empty archive-envelope fields remain compatible. The result retains UTC timestamps, monotonic elapsed durations, adapter provenance, resolver or destination evidence, individual attempts, summaries and explicit errors.

One local clock-quality observation accompanies the check. System UTC is not independently NTP verified; a provider's synchronization status and root dispersion proxy do not guarantee timestamp accuracy. Standalone checks do not automatically capture a route.

Compare terminal checks of the same type. Completed, failed, partial, cancelled and interrupted observations retain their status and missing evidence; active snapshots cannot be compared or reported. DNS, ICMP and throughput comparisons remain separate because their measurements have different meanings. Matching settings do not establish equal network conditions or prove causation, and changed outcomes are not ranked as improvements.

JSON/XML exports retain complete typed evidence. Imports create new local identities and preserve lineage provenance without launching a resolver, ping or other test. PDF/Word reports use the same normalized preview, fingerprints and resource budgets as [measurement reports](reports.md). Default redaction removes names, answer data, resolver and endpoint addresses, labels, notes, free-form errors, time-source context and raw output. Safe outcomes, response codes, flags, TTL, counts, nullable RTT and timing remain. Raw report output is separately opt-in and cannot bypass redaction. Unredacted reports and archive exports can contain sensitive network information.

SQLite remains in the persistent writable data volume. Back up the complete archive with the app stopped before upgrades or rollback. Keep the original copy; do not delete or overwrite it to make older software accept new record kinds. See [migration and rollback](sqlite-storage.md) and [maintenance](install/maintenance.md).

## Run again

Run again reads a saved configuration and creates a new execution with a new identity and immutable source lineage. It does not overwrite the source or copy captured measurements, native output, event history or timeline observations into a new result. Saved configuration notes and metadata remain configuration context.

Review the prepared settings before launching. Load settings lets you edit configuration before starting a separate execution; that path does not promise source lineage. Run again uses the validated saved-source plan and records its immutable source link. Current availability and admission rules are checked again. Incoming Run again executions wait initially for a client for at most 60 seconds; an accepted session remains subject to the existing server hard limit. Flent still requires its explicit Netserver confirmation. A saved result or prior confirmation does not establish that a peer remains reachable or compatible.

The browser supplies a request identity to prevent a double click or replayed response from starting duplicate work. Reusing an identity with changed intent conflicts. This local execution identity is not imported as reusable launch authorization. Cancellation and restart preserve the source and the newly committed capture; neither automatically retries traffic.

## API entry points

- `GET /api/checks` returns local capability hints without a network capability probe.
- `POST /api/checks` starts an explicitly requested typed check, for example `{"check_type":"dns","settings":{"target":"example.test","record_type":"A","resolver_mode":"system","transport":"udp","count":1,"timeout_ms":2000}}`.
- `GET /api/check-comparison?id=<first>&id=<second>` compares terminal checks of one type; `format=json` or `format=xml` exports the typed comparison.
- `GET /api/runs/<id>/rerun-plan` returns a configuration snapshot, source fingerprint and required confirmations without launching traffic.
- `POST /api/runs/<id>/rerun` submits the reviewed plan's `source_fingerprint`, a fresh `request_id` and any required confirmations.

Check records use the existing history, run status, cancellation and report endpoints. The API has no login protection; keep it on loopback or behind the intended authenticated access boundary.
