# Route and clock evidence before a test

This unreleased feature branch adds one bounded route capture and one read-only local clock-quality observation before each outgoing test. The preparation results are committed to SQLite before traffic begins. Missing tools, denied access, timeouts and incomplete replies remain evidence; they do not become zero values or prevent an otherwise valid throughput test. Stop during preparation cancels the owned probes and prevents traffic from starting. An archive write failure blocks the test.

## Which tests receive evidence

| Test | Capture policy |
| --- | --- |
| Single outgoing iPerf test | One route and clock observation before traffic |
| Both or matrix | Fresh evidence for each child that actually starts; the parent has no combined route or clock measurement |
| Actual Flent RRUL | The same preparation before the workload starts |
| Skipped child | No invented measurements or preparation |
| Passive incoming capture | No reverse route probes. The peer is known only after external traffic starts; clock evidence is labeled as observed after start |
| Historical or imported record without preparation | Displays not recorded; opening or importing a record sends no probes |

The old `settings.traceroute` field remains compatible with saved files. New execution follows the current preparation policy regardless of that historical option. Preparation and waiting are separate from measured traffic duration.

## Reading the hop map

Windows uses numeric `tracert`; Linux uses an available numeric `tracepath`. Each attempt has an overall 20-second limit and a maximum of 16 hops. The ordered map shows observed TTL/hop order, addresses, replies and probe RTTs. It retains unanswered hops, absent output, repeated addresses and RTT upper bounds such as less than 1 ms.

The connection view describes probe observations. It retains internal-to-internal hops, mixed IPv4/IPv6 replies and unanswered positions without inventing coordinates. The optional world view plots only approximate public-IP locations from a local database. Lines break across unlocated hops; they are visual connections between observations, not a physical cable route. Neither view identifies a throughput bottleneck or proves packet loss from missing replies. The probe route can differ from the path used by iPerf, and different replies at one hop need not come from one router.

### Optional offline public-IP locations

FlowBench reads an independently supplied **DB-IP City Lite MMDB** file. It makes no HTTP lookup, reverse DNS lookup, tile request or external IP submission. Missing, unreadable or unsupported data leaves locations unavailable while the ordered connection view and throughput test remain usable. Private, loopback, link-local, carrier-grade NAT, reserved and ambiguous IPv6 transition addresses are excluded from geographic lookup. Public results are approximate network locations, not device GPS or independently verified router positions.

The default file is `/data/geolocation/dbip-city-lite.mmdb`, within the persistent writable data volume. To use another local file, set `FLOWBENCH_GEO_DB` to its absolute path. For example, place the licensed file at `geolocation/dbip-city-lite.mmdb` inside the host directory already mounted at `/data`; restart the app after changing its environment. Keep the MMDB separate from the SQLite archive and include it in deployment backups if geographic reproducibility matters. The database is not bundled, downloaded or updated by FlowBench, and no provider account or API credential is required.

Obtain the dataset from the [official DB-IP City Lite download page](https://db-ip.com/db/download/ip-to-city-lite). The page requires acceptance of its licensing terms before download. Review and accept those terms yourself before supplying the file; this feature does not accept them on your behalf. DB-IP Lite uses [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) and requires attribution. FlowBench displays **IP Geolocation by DB-IP** with a link beside location results and in reports. Do not remove this credit when redistributing results. Lite coverage and accuracy are limited; inspect its build date and refresh it under the provider's terms when needed.

Each captured route retains the provider, licence, database type and build timestamp with its available locations. Opening or importing saved evidence never refreshes locations or submits addresses. An unavailable dataset never receives invented city/country coordinates. The bundled land outline comes from public-domain [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/); its pinned source and reproducible generation are recorded in `licenses/natural-earth.txt` and `scripts/build-world-map.py`.

### Synthetic browser examples

These captures use disposable saved records and invented approximate locations. They do not demonstrate licensed-dataset accuracy or an actual Internet route: [desktop world view](assets/preflight-world-desktop.jpg), [mobile overlapping markers](assets/preflight-world-mobile.jpg), [internal-only topology](assets/preflight-internal-mobile.jpg) and [mobile navigation](assets/preflight-navigation-mobile.jpg).

## Reading clock quality

The recorded UTC timestamp comes from the local operating-system clock. It is explicitly **not independently NTP-verified**. Monotonic timing measures preparation elapsed time; it does not establish wall-clock accuracy.

The app makes bounded, read-only queries to an available local time service, with a three-second total budget. It does not install a service, set the clock, change synchronization settings, request elevated privileges or contact a public NTP server. An ordinary container may share its host's clock while having no accessible time-service evidence; that case remains unavailable.

Daemon-reported synchronization, clock source, available signed offset and uncertainty are separate fields. A synchronized status alone does not provide an offset or an accuracy guarantee. Missing fields remain unavailable, while a measured zero remains zero. Chrony root dispersion and root delay retain their own meanings; imported observations are retained evidence rather than a new verification of the importing host. Windows output that cannot be interpreted safely remains malformed or unavailable instead of receiving a guessed translation.

The adapters follow the official [chronyc monitoring documentation](https://chrony-project.org/doc/4.6.1/chronyc.html), [Windows Time query documentation](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings) and [timedatectl documentation](https://www.freedesktop.org/software/systemd/man/247/timedatectl.html).

## Persistence, sharing and rollback

Route and preparation evidence use optional versioned fields in the existing SQLite run documents. No database schema conversion or external database is required. Pending checkpoints survive restart as interrupted evidence; tests are never resumed automatically. JSON/XML exports and imports retain the captured evidence, including each child of a job. Malformed new evidence is rejected before an import transaction writes any records. Historical rows are not rewritten to invent preparation.

Shareable reports include route/clock limitations. Default redaction hides hop addresses, geographic coordinates and place names, time-source identifiers, commands and raw native output. Review the preview before sharing unredacted evidence. JSON/XML archive exports retain full evidence and should be treated as sensitive.

Before upgrading or rolling back, stop traffic and the app, back up the entire archive consistently and rehearse on a copy. Older software may ignore optional preparation fields; keep a current compatible snapshot and exports for newer workflows. Never delete an archive to force migration. See [SQLite migration and rollback](sqlite-storage.md) and the [testing map](testing.md).
