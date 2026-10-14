# Optional Flent workloads

FlowBench can run actual [Flent RRUL](https://flent.org/tests.html) through an installed local Flent CLI on Linux. The ordinary iPerf tools, native ping diagnostics and reports remain usable without Flent. Native Windows execution is unavailable; use a Linux host or a separately prepared Linux container.

RRUL intentionally creates four upload and four download TCP flows plus separately identified UDP/ICMP RTT probes. It has no bandwidth cap. Confirm a controlled target with Netserver already running before selecting **Start Flent RRUL**. The adapter supports one RRUL at a time, 1–60 seconds of load, two seconds of pre/post measurement, and a bounded worker deadline. It does not install or configure a peer, credentials or firewall rules. FlowBench's managed incoming listener is an iPerf service, not Netserver.

The saved IP-family choice applies to Flent. An optional validated bind address is passed to both its data flows (`--local-bind`) and control connection (`--control-local-bind`); leaving it empty preserves Flent's normal address selection. These options select an existing local address and do not configure an interface or change network permissions.

## Local tools and prerequisites

The adapter detects `flent`, `netperf`, and `ping` or `fping` in the local PATH, records their availability and the reported Flent version, and exposes an unavailable message when a prerequisite is missing. Restart FlowBench after installing optional tools. Follow the [official installation guide](https://flent.org/intro.html#installation) and [command documentation](https://flent.org/options.html). Netperf requires demo support; plain iPerf peers cannot run RRUL. Runtime tests still determine whether detected dependencies and the peer actually work.

The optional verification environment uses Flent **2.3.0** and Netperf **2.7.1** source commit `afc51ff9764741da4ed6702651fba9d9c23f8557`, configured with `--enable-demo --enable-omni`. The exact source has an [MIT COPYING file](https://github.com/HewlettPackard/netperf/blob/afc51ff9764741da4ed6702651fba9d9c23f8557/COPYING). Older Netperf distributions may carry different licensing. The source archive checksum and configuration are pinned in [the test Dockerfile](../scripts/Flent.Dockerfile).

The portable application image includes iPerf, ping, tracepath and report dependencies. It does not include Flent or Netperf. [The derivative test image](../scripts/Flent.Dockerfile) installs them only to validate this integration with a disposable loopback peer; it is not uploaded as a release asset. This integration is merged into current main; the published rc.2 downloads remain unchanged.

For its loopback-only test namespace, the derivative applies [an auditable Netperf resolver patch](../scripts/netperf-loopback-resolver.patch) at two exact pinned-source sites. It omits `AI_ADDRCONFIG` only for the literal addresses `127.0.0.1` and `::1`, which otherwise fail resolution when no non-loopback interface exists. The fixture binds control and data flows to `127.0.0.1` and uses IPv4. Patch SHA-256: `1d08ee0d3154fa82af48d3f1ff44218c5a1317f0d95eed32721445656967451f`. CI prints this modified test-tool provenance; Flent itself and its native output remain unchanged. The test retains `--network none`, all capabilities dropped and `no-new-privileges`; no host settings or sysctls are changed. Ordinary installations use their preinstalled Netperf executable without this application making source changes.

## Saved evidence and cancellation

Native Flent result metadata, complete native JSON data, ordered elapsed-time series, original units, null samples, summaries, workload/tool provenance, and process logs are stored in the SQLite run document. Temporary native output stays in an owned archive subdirectory and is removed after ingestion; saved evidence remains. JSON/XML export and import retain the data. Imports validate derived summaries against native data and never execute commands.

Throughput and RTT stay separate. Unknown series units remain unknown, and missing samples remain null. Project comparisons use means from common, labeled native series; differing tools, workloads, versions and settings affect comparability. The iPerf live overlay uses iPerf intervals; Flent has a separate native-series chart.

Stop signals the adapter's own process group, including its spawned Netperf/probe processes. It leaves the independent peer running. Cancelled or failed results retain available native partial evidence and logs; app restart does not resume outgoing traffic. A tool exception or missing result is a failed/partial capture, never an invented successful measurement.

## Licensing boundary

Flent is GPLv3. Its [license explicitly excludes generated output](https://github.com/tohojo/flent/blob/v2.3.0/LICENSE). FlowBench invokes the external executable and imports output; it does not copy Flent source into application code. Optional installed distributions retain their own notices. The selected Netperf source is MIT licensed, and the test image preserves its complete notice. Distributing an image containing Flent entails the GPL source and notice obligations for that distribution; the ordinary FlowBench image avoids bundling it.

[Reports](reports.md) include completed Flent evidence with native series labels. [Measurement workflows](measurement-workflows.md) explain project baselines, annotations and persistent archives.

**Run again** can prepare a fresh RRUL execution from a saved record. It rechecks current tool support and requires a new explicit Netserver confirmation; saved native output is never replayed as a new measurement. See [Run again](network-checks.md#run-again).

## Reported target (branch preview)

The `feature/test-endpoints` branch, under implementation/review, labels a target found in retained Flent metadata as a **reported target**. A declared hostname is not proof of a measured numeric socket, interface or public address. Viewing it performs no resolution or network probe. See [endpoint context and privacy](test-endpoints.md). This addition is not in current main or existing rc.2 downloads.
