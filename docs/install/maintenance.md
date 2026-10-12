# Upgrade, back up or remove

[Installation index](README.md) · [Full SQLite migration and rollback guide](../sqlite-storage.md)

## Upgrade

1. Finish/stop active tests and stop the app.
2. Back up the complete archive outside its existing directory/volume.
3. Install the new source or matching architecture's image. Keep the existing archive and Compose project/volume.
4. Start, check `/api/ready`, then verify saved IDs, notes, logs, measurements, presets and listener settings.

rc.2 changes branding/copy, not the database schema. Upgrading older JSON/JSONL archives creates SQLite and imports records/presets once in a transaction. Source files remain untouched. Malformed/unsupported data stops migration without a partial import; retain the originals and repair a copy. Custom external PostgreSQL tables have no importer here.

## Back up

For native installs, stop the app and copy the entire `RUNS_DIRECTORY` to a new backup destination. For Compose:

```sh
docker compose stop app
container_id=$(docker compose ps --all --quiet app)
docker cp "$container_id:/data" ./flowbench-backup-new
```

Choose a new destination for each backup. Keep the database and any WAL/SHM files together; never delete journal sidecars or copy only a running database. Restore into a fresh archive/volume with the correct ownership, verify it, and keep the independent backup.

Full JSON/XML exports provide per-session interchange; they do not replace a backup of presets/listener configuration. Imports are additive and skip identical repeated imports.

## Roll back

Stop the app, keep its SQLite backup, and export a fresh legacy snapshot from the current version before switching to older code. Suppress saved listener startup during the export:

```sh
FLOWBENCH_RESUME_LISTENER=0 python -m flask --app app export-legacy /absolute/path/to/new-rollback-archive
```

Windows PowerShell equivalent:

```powershell
$env:FLOWBENCH_RESUME_LISTENER = '0'
.\.venv\Scripts\python.exe -m flask --app app export-legacy C:/FlowBenchBackup/new-rollback-archive
```

Restore the variable's previous value, or use a new terminal, before normal startup.

Use a new destination and require a successful export. Older releases may not understand Both parent jobs or listener resume. Use individual TCP/UDP exports where needed; preserve the newer SQLite backup for complete recovery. Container snapshot/copy steps are in the [full guide](../sqlite-storage.md#rollback).

## Remove while keeping data

Stop the app and verify a backup first. Native: retain the archive separately before removing the checkout or virtual environment. Container: `docker compose down` removes the app container while retaining named volumes; do not use `--volumes` or volume pruning. Loading/removing an image is separate from removing its data volume. No destructive uninstall is run automatically.
