$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$server = Join-Path $projectRoot '.venv\Scripts\waitress-serve.exe'
if (-not (Test-Path -LiteralPath $server)) { throw 'Create .venv and install requirements.txt first. See README.md.' }
Write-Host 'iPerf workspace: http://127.0.0.1:5000 (Ctrl+C to stop)'
& $server --listen=127.0.0.1:5000 --threads=8 wsgi:app
