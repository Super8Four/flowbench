# Installation troubleshooting

[Installation index](README.md) · [Configuration](configuration.md)

| Problem | Check |
| --- | --- |
| Python/launcher missing | Install the required Python runtime; use its actual executable if `py`/`python3` is unavailable |
| `.venv` startup executable missing | Create `.venv` and install `requirements.txt` from the checkout root |
| Engine unavailable | Check `IPERF_PATH`, executable permissions and `iperf3 --version`; restart after correcting it |
| Managed listener rejected by an older engine | Use the packaged 3.22 engine with full streaming JSON and server duration limits |
| Readiness returns 503 | Inspect `/api/status` and logs for missing engine, archive permissions, full disk or another writer; separately supported DNS/ICMP checks can remain usable when only iPerf is missing |
| Archive already owned | Stop the duplicate app; one process owns each archive |
| Wrong image architecture | Load the matching AMD64/ARM64 archive and verify `docker image inspect` |
| Checksum mismatch | Keep the file for diagnosis and download again; do not load or bypass the check |
| Listener bind/port failure | Inspect diagnostics, resolve the conflicting process/mapping, then explicitly restart |
| Incoming client fails while another runs | One incoming session at a time; wait for completion. Windows may report a reset instead of busy |
| Incoming client reaches another server | Only this managed listener creates FlowBench history |
| Browser start response is lost | Refresh saved status; use the offered Retry for that exact attempt. Run again uses a fresh identity for a new action and retains the same identity only for a transport retry; do not submit edited intent with the same key |
| UDP bidirectional on Windows | Use Linux or explicitly select upload/download |
| DNS unavailable or query timed out | Inspect resolver selection, returned status and individual attempts; no public resolver is automatically substituted. NXDOMAIN or SERVFAIL is captured DNS evidence, not a transport timeout |
| ICMP percentage unavailable or RTT shown as a bound | Unknown sends/replies cannot form a loss denominator; `<1 ms` stays an upper bound. ICMP nonresponse is not UDP/application loss; do not broaden permissions as a repair shortcut |
| Incoming Run again has no traffic | Confirm the fresh bounded wait, wait for native Running after preparation, and connect the controlled client before the initial idle timeout. It does not enable the persistent listener |
| Run again conflicts | Finish active work, reload a stale saved-source plan, or keep the original request identity/body for its transport retry; a changed intent needs a new action |
| World map has no locations | Provide a licensed readable local MMDB file or keep using ordered topology. Private/special addresses are intentionally excluded; no online fallback runs |
| Clock quality unavailable | The app may lack readable local provider evidence, especially in containers. System UTC is not independently NTP-verified; no clock/service configuration is changed |
| Import/migration failure | Retain data, validate a copy and supported schema; never delete a populated database to retry |

Avoid exposing the unauthenticated API as a troubleshooting shortcut. For developer checks, run `python -m unittest discover -s tests -v` from a source checkout with a verified engine; tests use disposable archives and loopback peers. Native container CI and physical-device limits are described in [verification](../verification.md).
