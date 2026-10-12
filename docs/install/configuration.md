# Configuration and ports

[Installation index](README.md) · [Incoming listener](../incoming-listener.md)

Native installs can use `.env` in the checkout. Container installs use the Compose environment and volume. Restart after changing the engine path or archive directory.

| Setting | Default | Purpose |
| --- | --- | --- |
| `RUNS_DIRECTORY` | Native `instance/runs`; container `/data/runs` | Writable archive directory; SQLite, events, notes, presets and listener settings |
| `IPERF_PATH` | Container `/usr/local/bin/iperf3`; native `bin` then PATH | Compatible iPerf3 executable |
| `WEB_PORT` | Compose host port 5000 | Host web publication, loopback by default |
| `COMPOSE_PROJECT_NAME` | Checkout folder name | Keep the original project name when upgrading so Compose reuses the same data volume |
| `IPERF_PORT` | Compose host port 5201 | Host TCP/UDP mapped port; container listener stays on 5201 |
| `IPERF_BIND_ADDRESS` | Compose host `127.0.0.1` | Host iPerf publication address |
| `FLOWBENCH_RESUME_LISTENER` | `1` | Set `0` for maintenance commands to suppress saved listener startup without changing its saved setting |

Native web binding is chosen by the startup command; `WEB_PORT` is a Compose substitution, not a native server setting. The archive needs write access. Container data is owned by UID/GID `10001:10001`. Use one app process per archive and a local filesystem; SQLite WAL does not support network filesystems.

Client tests need a reachable iPerf3 peer and do not require an inbound iPerf mapping. For incoming tests, explicitly enable the listener in Configuration. Its default `127.0.0.1` accepts clients inside the app/container only. To receive through an existing container mapping, choose a container IPv4 bind such as `0.0.0.0`; the host publication still controls reachability. Changing the listener bind does not open a firewall or change host mappings.

One incoming session is accepted at a time. An outgoing job can overlap, with interference recorded. Use a peer that stays listening for two connections when selecting Both. Windows UDP bidirectional is unsupported. Keep the API local or behind authenticated access; same-origin guards are not login protection.
