# FlowBench 0.1.0-rc.1

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=feature%2Fsqlite-storage)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Afeature%2Fsqlite-storage) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

**First prerelease · Measure. Compare. Verify.**

FlowBench provides portable SQLite-backed iPerf3 evidence, TCP/UDP/Both tests, default bidirectional client runs, automatic incoming session capture and the new gunmetal/navy/crimson interface. The GitHub repository is now `Super8Four/flowbench`; this release comes from the focused feature branch and does not merge to main.

## Downloads and installation

GitHub provides source ZIP/tar downloads from tag **v0.1.0-rc.1**. Additional assets are native Ubuntu container image archives for AMD64 and ARM64, each with Docker image metadata and SHA-256 checksums. Images use compatibility tag `iperf-workspace:local` and OCI labels for FlowBench version/source/exact revision. They contain application runtime assets, not user data volumes, private reference images, `.env`, local test logs or Windows binaries.

Use [installation instructions](setup-and-testing.md) and verify checksums before loading the matching image. The release is published only after the exact branch source passes both native CI jobs and the tag is checked against that tested commit. The release page identifies that source revision and CI run.

## Upgrade and rollback

Stop the previous app and back up the full archive/volume. Startup transactionally imports legacy JSON/JSONL runs and presets once without altering source files. SQLite resides at `/data/runs/archive.sqlite3`. Keep the same Compose volume/project for upgrades; never remove volumes to replace an image.

Before rollback, export a new legacy snapshot from the stopped current app with listener resume suppressed. Preserve a full SQLite backup. Pre-Both versions cannot interpret the parent job envelope, and pre-listener versions do not resume a managed listener. Individual TCP/UDP exports retain their measurements. See [migration and rollback](sqlite-storage.md) for exact procedures.

## Verification and known limits

Automated checks cover transactions/concurrency, errors/restart, import/export, real loopback protocol and listener traffic. Native CI tests the built image, non-root/read-only operation and persistent volume replacement. [Browser QA](verification.md) uses synthetic evidence on desktop and mobile; screenshots show real UI and are not target measurements.

There is no application login, automatic retention, full report generator, profile/threshold evaluator or authenticated instance coordination. R13-R17 remain pending. One incoming test at a time is supported; overlaps can interfere with measurements. UDP bidirectional is unavailable with the pinned Windows build. ARM64 CI is not proof of Raspberry Pi deployment or full canonical acceptance. No live services or access policies were changed.
