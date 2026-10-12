# Installation troubleshooting

[Installation index](README.md) · [Configuration](configuration.md)

| Problem | Check |
| --- | --- |
| Python/launcher missing | Install the required Python runtime; use its actual executable if `py`/`python3` is unavailable |
| `.venv` startup executable missing | Create `.venv` and install `requirements.txt` from the checkout root |
| Engine unavailable | Check `IPERF_PATH`, executable permissions and `iperf3 --version`; restart after correcting it |
| Managed listener rejected by an older engine | Use the packaged 3.22 engine with full streaming JSON and server duration limits |
| Readiness returns 503 | Inspect `/api/status` and logs for missing engine, archive permissions, full disk or another writer |
| Archive already owned | Stop the duplicate app; one process owns each archive |
| Wrong image architecture | Load the matching AMD64/ARM64 archive and verify `docker image inspect` |
| Checksum mismatch | Keep the file for diagnosis and download again; do not load or bypass the check |
| Listener bind/port failure | Inspect diagnostics, resolve the conflicting process/mapping, then explicitly restart |
| Incoming client fails while another runs | One incoming session at a time; wait for completion. Windows may report a reset instead of busy |
| Incoming client reaches another server | Only this managed listener creates FlowBench history |
| Browser request times out | Refresh status before retrying start/enable; the first request may have been accepted |
| UDP bidirectional on Windows | Use Linux or explicitly select upload/download |
| Import/migration failure | Retain data, validate a copy and supported schema; never delete a populated database to retry |

Avoid exposing the unauthenticated API as a troubleshooting shortcut. For developer checks, run `python -m unittest discover -s tests -v` from a source checkout with a verified engine; tests use disposable archives and loopback peers. Native container CI and physical-device limits are described in [verification](../verification.md).
