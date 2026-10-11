# iPerf workspace requirements

The goal is an AdminLTE interface for running iPerf, retaining evidence, comparing changes, and operating in an Ubuntu container on AMD64 or Linux ARM64, including a Raspberry Pi 5.

## Application acceptance criteria

- Test & export, History & import, Comparison, Server presets, and Accept incoming test have their own routes and AdminLTE sidebar navigation with submenus. Other pages' forms and tables are not rendered or loaded on the test page.
- The test page retains live throughput, local/remote report views, timestamped output, editable settings, labels, notes, JSON/XML export, and rerunning saved configurations.
- Finished tests show colored result cards using reported throughput, measured duration, outcome, and packet-loss detail when available. Missing values are not replaced with zero.
- History retains its compact dark table, settings/status, interval peaks, final averages, measured time, trends, search, open, import, and export actions.
- Comparison overlays two or three distinct completed tests on common elapsed-seconds and Mbps axes. Each test has a distinct color, upload is solid, and download is dashed. Remote reports are available when supplied by iPerf. No remote data is fabricated.
- Comparison retains measurement tables, change from baseline, settings mismatch warnings, and JSON/XML evidence exports. Both the older two-test export and the new three-test envelope can be imported.
- Public server presets and editable custom targets/settings remain available. Custom presets are saved from the test form. Additional arbitrary iPerf command arguments are outside the current scope.
- A one-test server session accepts incoming iPerf traffic, records actual peer parameters, and retains its output and measurements. Container port mappings must match the selected listening port.
- The client form offers route capture after throughput measurement. Capture is bounded to 16 hops and 20 seconds, uses Windows `tracert` or Linux `tracepath`, and records raw output and probe RTTs independently. Timeout, unavailable tools, cancellation, and missing replies are visible. Old imports remain compatible; API calls default to no route capture unless `traceroute: true` is supplied.

## Container acceptance criteria

- Retain Ubuntu 24.04 packaging with Python 3.12 and checksummed upstream iPerf3 3.22; build for the target architecture rather than shipping the Windows binary.
- Run as UID/GID 10001 without privileged mode or added capabilities. Store runs, logs, imports, notes, and custom presets in the persistent `/data` volume.
- Use one Waitress process, bounded subprocesses, signal cleanup, readiness checks, and the existing Compose hardening.
- Build, run the regression suite, and verify readiness, real loopback measurement, persistence, and shutdown on native AMD64 and ARM64 CI runners. Inspecting configuration alone does not establish runtime support.
- Actual Raspberry Pi deployment and access-boundary changes remain separate actions. On Docker versions before 28, review the documented loopback-publication limitation before treating those mappings as private.

## Running the Ubuntu container

On an approved Docker host, from the repository root:

```sh
docker compose config --quiet
docker compose build
docker compose up -d --wait
curl --fail http://127.0.0.1:5000/api/ready
```

Native builds on an ARM64 host produce ARM64 images. The CI workflow also produces architecture-specific Docker archives; load the matching archive with `gzip -dc iperf-workspace-linux-arm64.tar.gz | docker load` before using `docker compose up -d --no-build --wait`. Changing hosts requires moving the evidence volume separately. Avoid `docker compose down --volumes` when retaining history.

Web access defaults to host loopback on port 5000. Client tests need no inbound iPerf mapping. Server sessions use the TCP and UDP mappings on port 5201; change `IPERF_PORT` for the host-side port and `IPERF_BIND_ADDRESS` only after deciding the approved reachability. Keep the in-app server port at 5201 for that mapping. No host networking is required.

For traceroute, the image includes Ubuntu's [iputils-tracepath package](https://packages.ubuntu.com/noble/iputils-tracepath), which is available for ARM64. No host package installation is performed by the application.

Requirements are tracked in [GitHub issue #1](https://github.com/Super8Four/iPerfWeb/issues/1). See [Project state](project-state.md) for the verification boundary and [setup and testing guide](setup-and-testing.md) for operator workflows.
