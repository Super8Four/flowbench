# FlowBench verification and screenshots

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

## Current source and repeatable checks

Current main is unreleased `0.2.0-dev`, including route and read-only clock evidence, offline connection maps, DNS and ICMP checks, Run again, measurement workflows, actual optional Linux Flent and PDF/Word reports. PRs [10](https://github.com/Super8Four/flowbench/pull/10) and [21](https://github.com/Super8Four/flowbench/pull/21) were merged in order. Their combined code is commit `22f15addf6b0e232bbe6dcf44d57705ce9b546b3`; its file tree matches the tested feature commit `010177c5b85802dd89779e3d530965a5867efeba`. The latest published downloads remain rc.2. Use the exact source SHA and terminal CI run, rather than a changing badge, to identify tested code. [Testing](testing.md) maps meaningful checks and platform limits; [lifecycle](lifecycle.md) describes review and release evidence.

Run `python -m unittest discover -s tests -v` with the verified engine and dependencies. Source/Windows and native AMD64/ARM64 workflows exercise archive transaction/concurrency/error/restart, JSON/XML import/export, protocol/listener behavior, measurement workflows, report privacy, native Flent and report rendering. Node-dependent wrappers can skip in the Node-free image; actual controller behavior is exercised separately in the source job. Read the test summary, including skips, for the exact commit.

At combined main `22f15ad`, [source and Windows CI](https://github.com/Super8Four/flowbench/actions/runs/38509114032) and [native Ubuntu CI](https://github.com/Super8Four/flowbench/actions/runs/38509114070) completed successfully. Windows passed all 359 tests. Each native architecture passed 353 of 359 tests and skipped six Node-dependent wrappers; all six controller fixtures passed separately in source CI. Container persistence/restart, actual Flent rerun/retry/cancellation, report layout and image packaging checks passed on both architectures. These results do not describe an installation on a physical Pi.

The final feature report review at `010177c5` accounted for 212 pages across both architectures: 94 direct PDF pages and 118 LibreOffice-converted Word pages. Unchanged pages were matched to reviewed pixels; changed timestamp/footer regions and the corrected former blank-page position were inspected. The merged code tree is identical, and native report generation/layout checks also passed at `22f15ad`. Microsoft Word itself and every possible user dataset were not tested.

## Current browser captures

The refreshed gallery captures the actual merged application at `22f15ad` with a disposable SQLite archive and clearly labelled synthetic records. No private archive, reference image or external endpoint is published. Route/location examples are fixtures, not measured geography or activated DB-IP data. Graphics are self-contained SVG; screenshots remain raster images. The supplied original logo/PNG remain unchanged. Its dark-surface SVG uses the identical outlines, a light foreground/navy tint and unchanged crimson, with transparent clear space. The sidebar and mobile header render it directly without a white plaque. See [asset usage](brand-assets.md).

Targeted browser checks cover the seven application routes, desktop widths of 1440 and 1151 CSS pixels, mobile 375x842 and landscape 842x375, navigation opening/closing, keyboard focus and page overflow. Fourteen new JPEG captures replace the current gallery's older examples; historical image files remain available. Native route/clock providers and external network traffic were not invoked for these screenshots. These checks do not establish full WCAG certification, physical Pi deployment, external network performance or power-loss durability.

### Test and export

![Merged test page with measurement type and timezone controls](assets/merged-test-desktop.jpg)

### DNS and ICMP observations

![DNS query settings and captured response evidence](assets/network-dns-desktop.jpg)

![ICMP echo counts and explicit RTT bounds](assets/network-icmp-desktop.jpg)

### Fresh attempts

![Run again review with a fresh one-off incoming confirmation](assets/network-rerun-desktop.jpg)

### Persistent history

![Merged history with separately typed saved evidence](assets/merged-history-desktop.jpg)

### Typed comparisons

![DNS comparison preserves outcomes and captured-answer changes](assets/network-comparison-desktop.jpg)

### Before/after projects

![Merged project baseline with typed diagnostic evidence](assets/merged-projects-desktop.jpg)

### Incoming capture and configuration

![Stopped incoming capture in merged source](assets/merged-incoming-desktop.jpg)

![Merged configuration and storage status](assets/merged-configuration-desktop.jpg)

### Server presets

![Merged presets page without external traffic](assets/merged-servers-desktop.jpg)

### Mobile and landscape

![Merged mobile test page](assets/merged-test-mobile.jpg)

![DNS lookup settings on a narrow screen](assets/network-dns-mobile.jpg)

![Transparent logo in merged mobile navigation](assets/merged-navigation-mobile.jpg)

![Merged mobile landscape navigation](assets/merged-navigation-landscape.jpg)

## Report-download verification boundary

Actual UI requests produced valid redacted individual and five-run PDF/DOCX responses. Repeat requests, invalidated previews, the genuine stale-fingerprint rejection and controlled generation-error recovery were exercised. Supported browser download methods timed out for both the app and a plain local attachment control; final browser file saves remain unconfirmed. Captured HTTP response bodies are not browser-saved artifacts. This documented limitation was accepted for integration and is not reported as a passed browser save.

## Historical prerelease evidence

The rc.1/rc.2 tags retain their original verification pages and screenshots. At rc.2, 89 local Windows tests and both exact-source native jobs passed, with the original SVG preferred over PNG on a light sidebar plaque. Those screenshots and downloads describe that historical release. Earlier `integrated-*`, `preflight-*` and `svg-logo-*` captures remain in the repository as historical evidence; they are not relabelled as new captures. The [rc.2 release notes](release-0.1.0-rc.2.md) identify its source and checks; existing published tags/assets remain immutable.
