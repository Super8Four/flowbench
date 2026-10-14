# Planned network diagnostics

These entries record future scope. They do not enable scans, capture packets, install tools or authorize traffic. Standalone DNS, ICMP observations and Run again are merged into current main; see the [network-check guide](network-checks.md). The items below remain separate backlog decisions and are not available controls.

| Item | State | Intended evidence and limits |
| --- | --- | --- |
| [Configured STUN NAT and UDP checks #16](https://github.com/Super8Four/flowbench/issues/16) | Planned backlog | Use an operator-configured compatible STUN service; retain selected endpoint, protocol and actual observations. Distinguish blocked, unavailable and unsupported cases. No automatic public STUN dependency or universal NAT-type claim. |
| [Bounded selected-target packet capture #17](https://github.com/Super8Four/flowbench/issues/17) | Planned backlog | Explicit target, interface/filter, duration and byte limits; manual PCAP download and local summary of observed retransmissions/resets. Retain capture-loss, offload, encryption and direction caveats. A partial capture cannot prove all network events. |
| [TCP port connectivity #18](https://github.com/Super8Four/flowbench/issues/18) | Future possibility | Explicit selected host and bounded port list/timeouts. Distinguish connect, refusal, timeout, resolution and local errors; a timeout alone does not identify a firewall or failed service. |
| [Nmap remote profile #19](https://github.com/Super8Four/flowbench/issues/19) | Future possibility | Optional, manual, authorized and bounded scan. Ports and service responses are observations; OS identity is explicitly an estimate with confidence and limitations. Review dependency licensing, capabilities and permissions before adopting a tool or scan profile. |
| [Wi-Fi evidence #20](https://github.com/Super8Four/flowbench/issues/20) | Deferred | No implementation or implied host access. Define supported platforms, available native evidence and privacy boundaries before deciding scope. |

Before implementation, define source formats, units, missing-data behavior, cancellation, restart without automatic resumption, archive compatibility and platform acceptance examples. Review runtime privileges, dependency versions and notices under the existing [lifecycle process](lifecycle.md). Planning does not authorize host installs, additional capabilities, firewall changes or credential changes.

Packet captures, DNS records, endpoint identifiers and network profiles can contain sensitive information. Any future feature needs explicit capture handling, bounded local storage, user-controlled downloads, redaction limits and a retention decision. No automatic upload is planned. Authentication and retention remain the existing [open requirements](requirements.md), rather than separate duplicate roadmap implementations. Do not delete user data or silently broaden a selected target while evaluating a feature.
