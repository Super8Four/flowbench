# Third-party software

The local interface uses AdminLTE 4.0.0 and Bootstrap 5.3.8. Their distributed assets are bundled under `app/static/vendor/`; their MIT license notices are preserved alongside the assets. AdminLTE includes Bootstrap styles. No CDN request is needed at runtime.

- AdminLTE: https://github.com/ColorlibHQ/AdminLTE/tree/v4.0.0
- Bundled files: https://cdn.jsdelivr.net/npm/admin-lte@4.0.0/dist/
- Bootstrap: https://github.com/twbs/bootstrap/tree/v5.3.8

The bundled public server catalog in `app/data/public-servers.json` is derived from the parsed export at https://export.iperf3serverlist.net/listed_iperf3_servers.json, retrieved October 11, 2026 UTC. The source project is https://github.com/R0GGER/public-iperf3-servers and its website is https://iperf3serverlist.net/. Its MIT license notice is preserved in `app/data/LICENSE-public-iperf3-servers.txt`. Server locations, options, and capacities are catalog listings, not results from this application. Refreshing the list downloads data only.

The optional Windows installer downloads iPerf3 3.22 from the community build project https://github.com/ar51an/iperf3-win-builds/releases/tag/3.22 and verifies the published release asset SHA-256. Downloaded binaries are ignored by Git and are not part of this application's source distribution. iPerf3 is developed by ESnet under a BSD 3-Clause license: https://github.com/esnet/iperf/blob/master/LICENSE. Windows support comes from the community build; Linux Docker deployment can use the distribution's iPerf3 package.
