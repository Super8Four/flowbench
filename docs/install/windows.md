# Windows native installation

[Installation index](README.md) · [Configuration](configuration.md) · [Maintenance](maintenance.md)

Use 64-bit Windows, Python 3.12 with the `py` launcher, PowerShell and Git. Python 3.12 is the verified runtime. Download source from the release page or clone the tag:

```powershell
git clone https://github.com/Super8Four/flowbench.git
Set-Location -LiteralPath flowbench
git switch --detach v0.1.0-rc.2
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\install-iperf.ps1
.\scripts\start.ps1
```

The iPerf installer checks the pinned 3.22 archive before extraction into `bin`. You can instead set `IPERF_PATH` to a compatible executable; restart after changing it. If PowerShell blocks a script, inspect it and follow your organization's execution policy rather than disabling protection globally.

Open **http://127.0.0.1:5000**. In another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/api/ready
```

The response should contain `ready: true`. Stop with **Ctrl+C**. The startup script uses the local `.venv` and one server process with eight threads. To choose another local web port, use the same entry point directly:

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=127.0.0.1:5001 --threads=8 wsgi:app
```

Keep data outside the checkout if you plan to replace its directory. Set `RUNS_DIRECTORY` in `.env`, for example `C:/FlowBenchData/runs`; otherwise it uses `instance/runs`. See [configuration](configuration.md).

The Windows engine supports TCP bidirectional, but UDP/Both bidirectional is rejected by the app. Use upload/download or a native Linux image for that mode. The checked Windows startup/tests do not establish Docker Desktop operation; these container images need a Linux backend.
