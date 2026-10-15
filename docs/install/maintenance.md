# Upgrade, back up or remove

[Installation index](README.md) · [Full SQLite migration and rollback guide](../sqlite-storage.md)

## Upgrade

1. Finish/stop active tests and stop the app.
2. Back up the complete archive outside its existing directory/volume.
3. Install the new source or matching architecture's image. Keep the existing archive and Compose project/volume.
4. Start, check `/api/ready`, then verify saved IDs, notes, logs, measurements, projects/baselines, typed checks, source lineage, presets and listener settings.

Current main keeps SQLite schema 1 and adds versioned evidence in run documents plus local execution-request metadata. Reinstall the selected source version's requirements when upgrading a native environment. Published rc.2 remains unchanged. Upgrading older JSON/JSONL archives creates SQLite and imports records/presets once in a transaction. Source files remain untouched. Malformed/unsupported data stops migration without a partial import; retain the originals and repair a copy. Custom external PostgreSQL tables have no importer here.

## Back up

For native installs, stop the app and copy the entire `RUNS_DIRECTORY` to a new backup destination. For Compose:

```sh
docker compose stop app
container_id=$(docker compose ps --all --quiet app)
docker cp "$container_id:/data" ./flowbench-backup-new
```

Choose a new destination for each backup. Keep the database and any WAL/SHM files together; never delete journal sidecars or copy only a running database. Restore into a fresh archive/volume with the correct ownership, verify it, and keep the independent backup.

Full JSON/XML and project exports provide portable evidence interchange. They do not replace a complete archive backup: local execution-request mappings, presets and listener configuration are not portable launch authorization. A stopped, complete SQLite backup retains the local duplicate-request registry. Imports remap evidence/source identities, never register imported request tokens and never start a check or test. They remain additive and skip identical repeated evidence. Interrupted work is retained rather than resumed automatically.

## Roll back

Stop the app and retain a complete compatible SQLite backup before considering older code. Rehearse rollback on a fresh copy. An optional legacy snapshot preserves run documents/events for tooling; it does **not** convert new diagnostic-check, matrix or Flent evidence into formats an older release understands. Suppress saved listener startup if exporting that snapshot:

```sh
FLOWBENCH_RESUME_LISTENER=0 python -m flask --app app export-legacy /absolute/path/to/new-rollback-archive
```

Windows PowerShell equivalent:

```powershell
$env:FLOWBENCH_RESUME_LISTENER = '0'
.\.venv\Scripts\python.exe -m flask --app app export-legacy C:/FlowBenchBackup/new-rollback-archive
```

Restore the variable's previous value, or use a new terminal, before normal startup.

Use a new destination and require a successful export. Older releases can reject new record kinds or ignore optional evidence; do not point them at the only current archive or delete records to make them start. Prefer the untouched backup made before the upgrade for an older application, and preserve the current archive for complete recovery. Use individual compatible TCP/UDP exports only where the older version supports them. Container snapshot/copy steps are in the [full guide](../sqlite-storage.md#rollback).

## Remove while keeping data

Stop the app and verify a backup first. Native: retain the archive separately before removing the checkout or virtual environment. Container: `docker compose down` removes the app container while retaining named volumes; do not use `--volumes` or volume pruning. Loading/removing an image is separate from removing its data volume. No destructive uninstall is run automatically.
