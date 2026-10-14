# FlowBench requirements and acceptance boundaries

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

FlowBench's current main (`0.2.0-dev`) is a portable measurement/evidence workspace with iPerf3 throughput, standalone DNS and ICMP checks, projects, incoming capture, history/import, comparisons, reports and saved configuration. Ubuntu native AMD64/ARM64 containers and SQLite persistence are part of this development version. The GitHub repository is `flowbench`; existing local checkout, image tag and archive paths remain compatible. Published rc.1 and rc.2 downloads retain their original feature sets.

The separate `feature/test-endpoints` branch adds [captured endpoint and execution-context evidence](test-endpoints.md) for review. It is not included in the current-main implementation table or published rc.2 downloads. Its pull request records exact-source CI, browser/report checks and remaining acceptance limits.

| Area | Current implementation | Acceptance boundary |
| --- | --- | --- |
| Client configuration and measurement | TCP/UDP/Both, explicit directions, default bidirectional, bounded validation, logs/local and available remote reports | Peer compatibility, real network conditions and target performance need evaluation |
| Persistence and portability | SQLite transactions, unchanged evidence formats, additive imports, presets/notes, automatic legacy-file migration, legacy snapshot export | Backups, hardware durability and operator restore validation remain required |
| History and comparison | Add completed tests with saved colors, elapsed overlays across mixed durations, whole-run/common-window context, automatic SI display, notes and JSON/XML interchange | Practical data/rendering resource budgets apply; no physical unlimited-capacity claim or full accessibility certification |
| Incoming listener | Managed independent process, raw client captures, one incoming at a time, restart/stop/error and overlap metadata | No unlimited concurrency or guaranteed measurement isolation |
| Route evidence | Once-per-test bounded preflight tracepath/tracert, ordered observations, offline optional approximate public-IP map and read-only local clock context | Approximate network locations do not establish a physical route or bottleneck; no guaranteed clock accuracy or independently NTP-verified timestamps; passive incoming capture cannot pretrace an unknown peer |
| Standalone checks and reruns | Merged typed DNS query and ICMP echo observations, same-type terminal comparisons, reports and new executions from saved configuration | ICMP nonresponse is not UDP/application loss; unknown evidence stays unavailable; current peer/tool/permission support is rechecked |
| UI | Supplied FlowBench logo, tagline, gunmetal/navy/crimson, flat measured cards, responsive layouts, visible focus/errors | Targeted browser QA is not a full WCAG certification or full audit closure |
| R13 profiles/thresholds | Pending | Presets are not automatic threshold evaluation |
| R14 reports | Merged PDF/DOCX for completed measured tests and terminal same-type diagnostic checks, individual or selected comparisons, with normalized preview and redaction | Resource budgets, font coverage and evidence gates apply; rendered fixtures and targeted testing do not establish full canonical acceptance |
| R15 retention | Pending | No automatic deletion or retention policy |
| R16 authentication | Pending | Same-origin guards are not login or API authorization |
| R17 instance coordination | Pending | Managed listener/Both jobs are not authenticated instance-to-instance pairing, coordination or correlation |

Container criteria: Ubuntu 24.04/Python 3.12/checksummed upstream iPerf3 3.22, non-root UID/GID 10001, one Waitress process, read-only root, persistent writable `/data`, signal cleanup and readiness. CI must execute tests and lifecycle checks natively on both architectures at the exact release commit. A successful CI image is not a Raspberry Pi deployment, live access-boundary change or completion of all canonical requirements.

The [public tracking issue](https://github.com/Super8Four/flowbench/issues/1), [project state](project-state.md) and [planned network diagnostics](roadmap.md) provide context. STUN, packet capture, TCP port checks and Nmap remain backlog/future work; Wi-Fi is deferred. Private source requirements and visual references are not reproduced in the repository. Unverified findings from other work remain separate; this prerelease does not close them by implication.
