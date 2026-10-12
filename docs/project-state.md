# FlowBench project state

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=feature%2Fsqlite-storage)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Afeature%2Fsqlite-storage) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

This prerelease is prepared on **feature/sqlite-storage**, isolated from other work. The branch began at verified remote main `2d8d29c8ea0112543031cc566f3ca0fe847e29ed`. Main used UUID JSON/JSONL evidence directories and an optional unused PostgreSQL starter; this branch replaces archive persistence with SQLite and removes the scaffold without connecting to PostgreSQL.

SQLite implementation `708a577f2a7599030c15604cff03c749bbae9b70` passed 63 local tests and native AMD64/ARM64 [CI run 38261839934](https://github.com/Super8Four/flowbench/actions/runs/38261839934). TCP/UDP/Both implementation `4b9ccc9c24faf25358f1e8fd5e40a57901bcd54d` passed 78 local tests and native [CI run 38264847645](https://github.com/Super8Four/flowbench/actions/runs/38264847645). These are historical checks of those exact commits, not substitutes for final release validation.

The release adds managed incoming capture, saved listener configuration, independent outgoing execution, overlapping-run labels, failure/restart evidence and FlowBench branding. UI assets are local and the supplied logo is preserved. The project-scoped upstream UI/UX skill is pinned and attributed; it is excluded from the runtime image.

Current test sources cover transactional migration/retry, rollback export, locks/full disk/denied writes, interrupted recovery, concurrency, imports/exports, protocol jobs and real bounded raw-client listener sessions. The container check exercises disposable-volume replacement, enabled listener resume, no duplicated history and graceful incoming/outgoing stop. [Verification](verification.md) describes browser checks and the exact-commit release gate.

No live database, old desktop checkout, main branch, credentials, host firewall or private requirements vault is modified by this work. No Raspberry Pi deployment occurred. R13-R17 and broader acceptance/audit gaps in [requirements](requirements.md) remain open. Authentication, automatic retention and full report generation are not advertised as implemented.
