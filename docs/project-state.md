# Project state

Recorded October 11, 2026. The current implementation extends repository main at `2ade0ccd5dd47d8e0ec3d89486ee39bee3c6e617`. The earlier isolated packaging experiment was based on `03c16a0`; its archive was not transferred and is not the current application package.

## Implemented behavior

The Flask/AdminLTE application has focused test/export, incoming-server, history/import, comparison, and preset pages with sidebar submenus. Completed runs show colored measured-result cards. Comparison overlays two or three runs and preserves metric changes, settings warnings, local/remote reports, and JSON/XML evidence. Existing persistent output, native/saved import, duplicate detection, labels, notes, public/custom presets, and single-test server sessions are retained.

Client tests can capture a bounded route after throughput measurement. Windows uses `tracert`; Ubuntu uses unprivileged `tracepath`. Raw output, responding hops, and probe RTTs are archived separately. Tests cover missing tools, timeout, cancellation, and import/export preservation without external route probes.

## Container goal and verification boundary

Ubuntu 24.04 packaging retains Python 3.12, checksummed upstream iPerf3 3.22, a non-root runtime, one Waitress process, signal cleanup, read-only root filesystem, persistent `/data`, and `/api/ready`. Compose defaults web and TCP/UDP iPerf mappings to loopback. PostgreSQL is optional. The updated workflow builds and tests native AMD64 and ARM64 images, checks architecture and route-tool availability, runs regression and lifecycle checks, and packages separate image archives.

Local application verification uses the existing Windows Python/iPerf installation and loopback-only throughput. This machine has no Docker or Podman runtime, so it cannot build or execute the image locally. CI results must be inspected for the specific published commit before claiming image verification. Configuration inspection alone does not establish ARM64 execution.

An earlier authorized read-only SSH inventory verified the target Pi's `aarch64` architecture, Docker `26.1.5+dfsg1` daemon on `linux/arm64`, and Compose `2.26.1-4`. No application source transfer, image build, smoke test, or deployment on that Pi started. Buildx and target storage were not checked. Credentials are not part of repository documentation.

Docker documents a [loopback-publication limitation before version 28](https://docs.docker.com/engine/network/port-publishing/), so the target's access boundary needs review before deployment. No host, network, firewall, or authentication settings were changed. Future Pi deployment remains a separately authorized action.

See [requirements](requirements.md), [issue #1](https://github.com/Super8Four/iPerfWeb/issues/1), and [setup and testing guide](setup-and-testing.md).
