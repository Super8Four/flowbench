# FlowBench requirements and acceptance boundaries

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=feature%2Fsqlite-storage)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Afeature%2Fsqlite-storage) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

FlowBench's current scope is a portable iPerf3 measurement/evidence workspace with focused Test & export, Incoming capture, History & import, Compare tests, Server presets and Configuration pages. Ubuntu native AMD64/ARM64 containers and SQLite persistence are part of this prerelease. The GitHub repository is now `flowbench`; existing local checkout, image tag and archive paths remain compatible.

| Area | Current implementation | Acceptance boundary |
| --- | --- | --- |
| Client configuration and measurement | TCP/UDP/Both, explicit directions, default bidirectional, bounded validation, logs/local and available remote reports | Peer compatibility, real network conditions and target performance need evaluation |
| Persistence and portability | SQLite transactions, unchanged evidence formats, additive imports, presets/notes, automatic legacy-file migration, legacy snapshot export | Backups, hardware durability and operator restore validation remain required |
| History and comparison | Peaks/averages/runtime, search, notes, two/three-test overlays, settings warnings, JSON/XML evidence | Broader chart interaction and accessibility acceptance are not established by this release |
| Incoming listener | Managed independent process, raw client captures, one incoming at a time, restart/stop/error and overlap metadata | No unlimited concurrency or guaranteed measurement isolation |
| Route evidence | Post-test bounded tracepath/tracert with raw output, hops and probe RTTs | Not throughput, iPerf RTT or authenticated topology discovery |
| UI | Supplied FlowBench logo, tagline, gunmetal/navy/crimson, flat measured cards, responsive layouts, visible focus/errors | Targeted browser QA is not a full WCAG certification or full audit closure |
| R13 profiles/thresholds | Pending | Presets are not automatic threshold evaluation |
| R14 reports | Pending | Existing JSON/XML evidence exports are not full designed report generation |
| R15 retention | Pending | No automatic deletion or retention policy |
| R16 authentication | Pending | Same-origin guards are not login or API authorization |
| R17 instance coordination | Pending | Managed listener/Both jobs are not authenticated instance-to-instance pairing, coordination or correlation |

Container criteria: Ubuntu 24.04/Python 3.12/checksummed upstream iPerf3 3.22, non-root UID/GID 10001, one Waitress process, read-only root, persistent writable `/data`, signal cleanup and readiness. CI must execute tests and lifecycle checks natively on both architectures at the exact release commit. A successful CI image is not a Raspberry Pi deployment, live access-boundary change or completion of all canonical requirements.

The [public tracking issue](https://github.com/Super8Four/flowbench/issues/1) and [project state](project-state.md) provide context. Private source requirements and visual references are not reproduced in the repository. Unverified findings from other work remain separate; this prerelease does not close them by implication.
