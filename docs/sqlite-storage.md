# FlowBench SQLite storage, migration and rollback

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

## Storage design

FlowBench uses the renamed `Super8Four/flowbench` repository and retains compatibility paths. Base commit `2d8d29c8ea0112543031cc566f3ca0fe847e29ed` stores evidence in UUID directories containing `run.json` and `events.jsonl`, plus `server-presets.json`. PostgreSQL/SQLAlchemy and `Measurement` are unused starter code. This branch removes that scaffold and its optional Compose service. It never connects to, alters or deletes PostgreSQL data. Old `DATABASE_URL`/`POSTGRES_*` values are ignored. If custom code populated starter tables, retain their separate backup; this branch has no custom PostgreSQL importer.

SQLite lives at `RUNS_DIRECTORY/archive.sqlite3`: `instance/runs/archive.sqlite3` locally and `/data/runs/archive.sqlite3` in the existing Compose `iperf_data` volume. Ubuntu 24.04, Python 3.12, non-root execution, the read-only container root and AMD64/ARM64 packaging remain. Python's bundled `sqlite3` driver needs no database server or additional database wheel.

Schema version 1 contains `runs` (original JSON documents keyed by UUID), `events` (JSON events keyed by UUID/sequence with foreign keys) and `metadata` (preset catalog, listener configuration, migration marker and readiness probe). Original evidence fields/types and JSON/XML schemas are retained. The bundled public catalog is used until a catalog is saved. `/api/status` also identifies the backend and database path.

Each captured event and its corresponding measured state commit before HTTP readers see them. Single/comparison imports use one transaction; notes/presets publish only after commit. SQLite failures return HTTP 503. Capture failures fail the run, disable readiness and block further tests until repair/restart. If saving final evidence fails, the last committed snapshot remains. Unfinished snapshots become interrupted on restart; uncaptured output cannot be reconstructed.

Connections use foreign keys, a five-second lock timeout, WAL and `synchronous=FULL`. App connections and close/checkpoint operations are serialized, avoiding overlapping app writes/checkpoints on older bundled SQLite versions with the [WAL reset race](https://www.sqlite.org/wal.html#walreset). The existing writer lock permits one app process with Waitress threads. Do not open another app/database writer on the same archive. Use a writable local filesystem/normal Docker volume; [WAL does not support network filesystems](https://www.sqlite.org/wal.html). Durability also depends on storage hardware and filesystem behavior.

## Upgrade and import

1. Finish/stop active tests and stop the old app. Back up the entire archive outside its volume. Keep the old checkout/image identity and Compose project name.
2. Build this branch and start against the same directory/volume. Schema creation and a one-time legacy import run before readiness; no `init-db` step is needed.
3. Verify `/api/health`, `/api/ready`, older IDs/dates/notes/measurements/logs, presets and JSON/XML exports against the backup.

Migration validates all legacy records/events/presets and imports them in one transaction. IDs, dates, evidence fields and provenance survive. Event counts reflect complete retained events. A torn final JSONL event is unavailable, as with the old reader; earlier malformed lines fail migration. Unfinished runs become interrupted during recovery. No source file is modified or removed. The migration marker commits with the import, preventing old files from reverting later SQLite changes.

Malformed input, conflicts or unsupported schemas fail startup. Failed migration rows roll back and the marker remains unset, allowing retry after repairing a copy of the source. Never delete a database to retry after it contains newer evidence. Old files remain alongside SQLite, so initial usage includes both copies. No automatic retention/deletion occurs.

For a different host, stop both apps and copy a complete backup into a **fresh** writable archive/volume. Container ownership must be UID/GID `10001:10001`. Never copy over existing evidence. Additive JSON/XML imports still assign new local IDs and skip repeated identical imports.

## Backup and restore

Stop the app and copy the **entire** `RUNS_DIRECTORY` using the [setup guide](setup-and-testing.md#keep-and-back-up-the-archive). Keep `archive.sqlite3` and any `-wal`/`-shm` sidecars together. Copying a running database alone is unsafe; [WAL is part of persistent state](https://www.sqlite.org/wal.html#the_wal_file). Never manually delete sidecars. Normal connections close/checkpoint automatically.

Restore into a fresh archive/volume with the same ownership and verify evidence before retiring any copy. Full JSON/XML exports are additional per-run backups; metadata/summary-only files cannot recover missing raw logs.

## Rollback

Old legacy files do not include new evidence or edits made after upgrade. Export a fresh legacy snapshot first. Export uses one read transaction, refuses an existing destination and never overwrites/deletes source data. If it fails, retain the partial destination for diagnosis and retry into another new directory. Use only an export that reported success.

With the app stopped, from this branch's checkout/Python environment (set `RUNS_DIRECTORY` first for a non-default source):

```sh
FLOWBENCH_RESUME_LISTENER=0 python -m flask --app app export-legacy /absolute/path/to/new-rollback-archive
```

For Compose, choose a fresh snapshot name and keep the existing volume:

```sh
docker compose stop app
docker compose run --rm --no-deps -e FLOWBENCH_RESUME_LISTENER=0 app python -m flask --app app export-legacy /data/rollback-snapshot
container_id=$(docker compose ps --all --quiet app)
docker cp "$container_id:/data/rollback-snapshot" ./rollback-snapshot
```

Use a separate checkout of `2d8d29c8ea0112543031cc566f3ca0fe847e29ed` or the saved old image and a fresh archive/Compose project. Point the old local app's `RUNS_DIRECTORY` at the snapshot, or copy it into the fresh old container's `/data/runs` using the README procedure. Restore ownership, start only one app and verify IDs, dates, notes, logs and presets. Keep the SQLite backup/volume. Rollback removes no PostgreSQL volumes or other data.

## Verification scope

Tests cover migration/idempotence, malformed-source rollback/retry, rollback export, restart recovery, comparison-import atomicity/deduplication, concurrent capture/notes/import/export, lock timeout, actual SQLite page-limit exhaustion, denied writes, killed transactions, corrupt/future-schema rejection and integrity/foreign-key checks. Existing tests cover bounded localhost client/server traffic, cancellation/failure, local/remote results and JSON/XML interchange.

Native Ubuntu AMD64/ARM64 CI also builds the image and checks migration in a disposable volume, SQLite under a read-only root, import/export/deduplication, presets, integrity, container replacement and graceful active-test stop. Results apply only to their exact source commit. They do not establish target deployment, physical power-loss/disk-exhaustion recovery, external-client mappings or jRPI-5 performance. These checks use no live database/target host.

The original SQLite change covers storage portions of R03/R04/R11/R12. The measurement-workflows feature branch additionally stores diagnostic/native Flent evidence, project metadata, tags and timeline notes in the existing schema 1 JSON documents/metadata table. Legacy snapshot export includes `projects.json` to retain project catalogs and baselines; old applications may ignore that extra file. Keep the full SQLite backup for a complete restore.

R14 PDF/Word reports are implemented on this unmerged feature branch. Profiles/thresholds, retention and authentication remain pending. See [workflows](measurement-workflows.md), [reports](reports.md) and [remaining requirements](requirements.md).

A saved enabled listener resumes on normal app startup. Use the maintenance override shown above for exports. Legacy snapshots retain incoming evidence but do not recreate managed listener configuration on an older release; preserve the complete SQLite backup for that metadata. R17 authenticated instance coordination also remains pending.
