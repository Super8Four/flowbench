# iPerf

Python / Flask backend, PostgreSQL database, and an HTML/CSS/JavaScript web interface.

## Setup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with your database credentials. Keep this file private. If your password includes URL special characters, URL-encode it in `DATABASE_URL`.

## Start PostgreSQL

With Docker Desktop installed and running:

```powershell
docker compose up -d db
```

Alternatively, use a local PostgreSQL installation: create a login and database, then update `DATABASE_URL` in `.env` to match. The example uses login `iperf` and database `iperf`.

## Initialize and run

```powershell
.\.venv\Scripts\python.exe -m flask --app wsgi init-db
.\.venv\Scripts\python.exe -m flask --app wsgi run --debug
```

Open http://127.0.0.1:5000. The starter page runs even when PostgreSQL is unavailable and displays its connection status. The database initialization command requires PostgreSQL to be running.

For a local run without the development debugger:

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:5000 wsgi:app
```

## Structure

- `app/__init__.py`: application factory, routes, health endpoint, and database initialization command.
- `app/models.py`: starter Measurement model; extend with application models.
- `app/templates/`: HTML pages.
- `app/static/`: CSS and JavaScript.
- `compose.yaml`: optional PostgreSQL service with persistent storage.

The starter does not yet run iPerf network tests. Add application features and database migrations as the schema evolves; `init-db` only creates missing tables.
