# Test endpoints and execution context

This guide describes Test endpoints in current main (`0.2.0-dev`), delivered through [PR24](https://github.com/Super8Four/flowbench/pull/24). Published rc.2 downloads remain unchanged and do not contain this addition. The [verification record](verification.md) identifies exact-source CI, browser checks, screenshot provenance and rendered-report evidence; the PR records integration and post-merge checks.

## Read the selected test's evidence

The **Test endpoints** panel distinguishes the machine/process running a test, the endpoints reported by its tool and the web request that started it. A requested hostname or bind setting describes configuration. A numeric address from retained tool output describes an observed endpoint. Missing evidence remains unavailable rather than being filled from today's settings or viewer.

| Context | Meaning and limits |
| --- | --- |
| Execution hostname | Captured from the local operating system when the run is created. In Docker this can be the container hostname. It identifies the process's hostname namespace, not a verified physical device, source IP or public egress address. No hostname lookup is performed. |
| Local and remote sockets | Numeric address, port and family reported by an iPerf start. Each pair belongs to its named reporter and observer perspective; parallel streams can have separate sockets. |
| Accepted control peer | iPerf's separately reported accepted connection. Its port is control-connection evidence, not a measured data-stream port. |
| Captured initiating web peer | The server-observed web peer when a new execution was requested. It is separate from the traffic sockets and can be a reverse proxy rather than the user's browser. A request port is not captured. |
| Current viewing context | The current request's execution hostname and web peer. This is labelled separately and never substituted into a selected historical test. |

No public-IP service, reverse DNS request, external address submission or NAT discovery is used. A local or container address can differ from the address a remote peer observes after NAT. A proxy can hide the browser's address. Forwarded headers do not establish a captured initiator, and this feature does not change proxy trust or host networking.

## iPerf direction and reporter perspective

The local iPerf process and an available peer report can describe the same connection from different namespaces. Both are retained with their reporter labels. **Local** and **remote** always mean local and remote to that reporter. Reverse or bidirectional traffic does not swap those labels: direction describes which way data travels, not which socket belongs to the reporting process.

For example, an outgoing client can report its container address and ephemeral local port paired with the peer's numeric address and server port. The peer can report a different remote address because it sees the client after NAT. The app preserves both observations; it does not guess a mapping between them or claim that one proves the public path.

Both and matrix parents group tests and have no invented combined socket measurement. Inspect each child for its own captured endpoints and creation context. Managed incoming captures retain the accepting process's perspective, but no initiating web request is inferred for a session started by an external iPerf client. An explicit incoming **Run again** is a new web-requested one-test wait and receives its own context.

## DNS, ICMP and Flent

- **DNS lookup:** destination evidence comes from actual query attempts, including the selected numeric resolver, its port and UDP or TCP transport. A question name and returned answer address are DNS data; they are not the socket destination of the query. A retained UDP attempt and TCP fallback can appear separately.
- **ICMP echo check:** the retained resolution/pinned destination identifies the numeric address actually selected for probes. ICMP has no TCP/UDP port, so the port stays unavailable. It does not become port zero or an inferred service.
- **Flent:** a host declared in retained native tool metadata is labelled as a reported target. A declaration alone does not establish measured socket addresses or ports, and viewing it sends no lookup or probe.

Tool absence, text-only output, malformed native fields, failed starts and incomplete capture can leave some or all endpoint details unavailable. The original output remains available as evidence. Lists are bounded; reaching a metadata limit reports partial availability without truncating or rewriting the original native source.

Endpoint availability is separate from the test's capture outcome. A completed test can have partial or unavailable endpoint details; that does not turn missing addresses into a failed measurement or a guessed socket.

The follow-up `fix/endpoint-partial-evidence` branch is under review. The merged PR24 version can incorrectly upgrade partial availability when valid sockets coexist with malformed entries and a later update has no reconciling socket evidence. The proposed correction preserves the warning through end-only output and finalization. Fresh matching observations can still fill unknown socket fields; empty starts and another reporter's observations cannot erase the warning. It requires no schema migration and does not rewrite or repair older archived labels.

## History, copies and provenance

New captures retain optional versioned endpoint metadata alongside the existing run document. Streaming iPerf start evidence commits with its event before it is shown. A later end-only result must not erase earlier saved socket observations. Local and peer observations are merged without moving either into the other's namespace.

History uses a compact availability/count/family summary. The selected detail view exposes the full retained tuples and clearly distinguishes captured archive, imported archive, preserved native fallback and not-recorded evidence. Copy controls copy the displayed captured value; copying is not a connectivity test. Selectable text remains available when clipboard access is denied.

Old/imported records without endpoint metadata can derive a **view-only** fallback from preserved structured native starts. Their execution hostname remains unavailable; the current hostname is never inserted. No raw-log/event scan or network query runs merely to populate history. Opening, exporting or importing a record does not rewrite it, refresh endpoints or initiate traffic. Imported host and initiator values remain supplied historical provenance, not authenticated observations of the importing machine.

**Run again** creates new endpoint and initiating-request context for the new attempt. An identical transport retry returns the already accepted attempt and its original context; it does not refresh that context from the retrying browser. Neither path overwrites the source record. The current viewer can therefore differ legitimately from the original initiator.

## Sharing and privacy

Full **JSON/XML run, job, comparison and project exports are sensitive evidence**. They can include execution hostnames, socket/control/destination addresses, ports, IPv6 scope identifiers, initiating web peers and native/raw output. Review the destination and access boundary before sharing them.

PDF/Word reports redact sensitive endpoint and initiating-context fields by default, including nested copies and raw opt-in content. Hostnames, numeric/string addresses, ports, scope identifiers, socket IDs and arbitrary future endpoint/context fields are removed. Safe availability, counts, address-family and protocol categories can remain. **Include raw evidence** does not bypass redaction. Unredacted reports label reporter perspective and execution/proxy/NAT limits and can contain sensitive network context. Endpoint/context changes invalidate the report preview fingerprint; review a fresh preview before downloading.

The current viewing request is not substituted into a historical report. See [report options and privacy](reports.md) for the separate full-archive and readable-report formats.

## Import, storage and rollback

The endpoint additions use optional versioned fields within **SQLite schema 1**; they require no database-server migration or data-volume replacement. JSON/XML imports validate structured metadata, context and bounded values before the existing atomic write. Invalid versions, shapes, scopes or directly contradictory normalized/native socket evidence reject the whole import. Missing final start output alone does not invalidate retained streaming observations. Existing IDs are remapped through normal import rules, with provenance preserved and no execution of imported commands.

Keep the persistent writable data volume and use a complete stopped archive backup, including any WAL/SHM sidecars. A schema number alone does not guarantee that an older application understands optional endpoint fields or newer record kinds. Rehearse rollback on a fresh compatible copy, retain the untouched current archive and never remove user evidence to make older code accept it. See [SQLite backup and rollback](sqlite-storage.md).

## API and validation boundary

- `GET /api/context` provides current viewing context only.
- `GET /api/runs/<id>` retains optional raw `endpoints` and `initiator_context` and adds a read-only `endpoint_view` for selected evidence and fallback provenance.
- Portable exports retain stored evidence and native sources; the computed `endpoint_view` is not written back or added as a stored/exported field.

Endpoint parsing, validation, import, views and reports perform no hostname resolution. New capture code obtains its hostname from the local OS; new initiating context comes from the server-observed request peer, independently of execution intent and user-supplied JSON.

Verification covers controlled IPv4/IPv6 and multi-stream native evidence, reverse/bidirectional reporter checks, passive incoming and typed destinations, cancellation/restart/import atomicity, retry context preservation, historical fallback without archive mutation, and redacted PDF/Word inspection. Actual desktop/mobile keyboard, copy and polling/disclosure checks complement controller fixtures. Consult the pull request for the exact source, passed/skipped/unrun checks and remaining limits; this guide does not certify a deployment or complete requirements acceptance.
