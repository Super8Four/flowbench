# Third-party software

Report generation uses ReportLab 4.4.9 (BSD 3-clause) and python-docx 1.2.0 (MIT). Complete upstream notices are retained in `app/third_party/licenses/`, together with the Bitstream Vera font license. The PDF renderer embeds Vera and attaches its license to generated PDFs. Installed dependency distributions also retain their notices.

The optional Flent integration invokes an external Flent executable; Flent source is not copied into application code or the portable runtime image. Flent 2.3.0 is GPLv3, and its license excludes generated output. The test-only derivative installs Flent and preserves its distribution; its Netperf source is pinned to MIT-licensed commit `afc51ff9764741da4ed6702651fba9d9c23f8557` with the complete COPYING notice preserved. See [the integration and distribution boundary](docs/flent.md).

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](docs/assets/badges/container.svg)](docs/setup-and-testing.md) [![Architecture](docs/assets/badges/platform.svg)](docs/setup-and-testing.md) [![Python](docs/assets/badges/python.svg)](docs/setup-and-testing.md) [![SQLite](docs/assets/badges/storage.svg)](docs/sqlite-storage.md) [![iPerf3](docs/assets/badges/engine.svg)](docs/protocol-tests.md)

The project-scoped UI/UX Pro Max skill is from [Next Level Builder](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/tree/7246805992a42b7bba37d4cac83b01416f0c6c44), pinned at `7246805992a42b7bba37d4cac83b01416f0c6c44`. Its MIT copyright/license notice is preserved at `.agents/skills/ui-ux-pro-max/LICENSE`. It is a development guidance tool, not an application runtime dependency. No global CLI installation is required.

The local interface uses AdminLTE 4.0.0 and Bootstrap 5.3.8. Their distributed assets are bundled under `app/static/vendor/`; their MIT license notices are preserved alongside the assets. AdminLTE includes Bootstrap styles. No CDN request is needed at runtime.

- AdminLTE: https://github.com/ColorlibHQ/AdminLTE/tree/v4.0.0
- Bundled files: https://cdn.jsdelivr.net/npm/admin-lte@4.0.0/dist/
- Bootstrap: https://github.com/twbs/bootstrap/tree/v5.3.8

The bundled public server catalog in `app/data/public-servers.json` is derived from the parsed export at https://export.iperf3serverlist.net/listed_iperf3_servers.json, retrieved October 11, 2026 UTC. The source project is https://github.com/R0GGER/public-iperf3-servers and its website is https://iperf3serverlist.net/. Its MIT license notice is preserved in `app/data/LICENSE-public-iperf3-servers.txt`. Server locations, options, and capacities are catalog listings, not results from this application. Refreshing the list downloads data only.

The optional Windows installer downloads iPerf3 3.22 from the community build project https://github.com/ar51an/iperf3-win-builds/releases/tag/3.22 and verifies the published release asset SHA-256. Downloaded binaries are ignored by Git and are not part of this application's source distribution. iPerf3 is developed by ESnet under a BSD 3-Clause license: https://github.com/esnet/iperf/blob/master/LICENSE. Windows support comes from the community build.

The Ubuntu container builds ESnet's upstream iPerf3 3.22 source archive from https://downloads.es.net/pub/iperf/iperf-3.22.tar.gz after verifying the published SHA-256 `1c0d0fb02c52626111d6e132db80edfbf27bbaff8bd9245df2a371dcb0b35a92`. Its upstream license is installed at `/usr/local/share/licenses/iperf3/LICENSE` in the image. Compiler tools and source archives stay in the build stage. Ubuntu packages and Python dependencies retain their distributed license metadata.
