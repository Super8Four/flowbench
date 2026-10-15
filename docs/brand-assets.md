# FlowBench brand assets

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

**Measure. Compare. Verify.**

| Asset | Use |
| --- | --- |
| [Original FlowBench SVG](../app/static/brand/flowbench-logo.svg) | Unchanged supplied outlines and colors for light surfaces |
| [Dark-surface SVG](../app/static/brand/flowbench-logo-dark.svg) | Same 13 paths, light foreground/navy tint and original crimson; transparent, without a plaque |
| [Original FlowBench PNG](../app/static/brand/flowbench-logo.png) | Unchanged original raster asset and favicon fallback |

The original SVG and PNG retain their 2172×724 canvas. The dark-surface variant preserves every path and its proportions, trims unused canvas to a 2048×432 view box with clear space, and uses the existing light text color plus a lighter navy tint so the wordmark is readable on gunmetal. Crimson remains unchanged. Do not stretch or invert the artwork. The application places this transparent SVG directly on the sidebar and mobile header; no white wrapper, shadow, border or raster image supplies its background. The sidebar image has `alt="FlowBench"`; the mobile home link has an accessible name. Favicon links still offer both original formats.

Both SVGs are self-contained: no scripts, fonts, embedded images, CSS dependencies or external resources. They are served as `image/svg+xml`. README branding chooses the dark SVG for dark readers and the original SVG for light readers, with no background panel. Real browser screenshots remain raster captures, rather than reconstructed SVG mockups. Security, geometry and serving checks run in the application suite; actual desktop/mobile pixels and keyboard navigation are checked separately.

The transparent dark-surface integration is included in 0.2.0-rc.1. Older rc.2 downloads retain their original presentation. Existing rc.1/rc.2 tags and assets remain immutable. See [release notes](release-0.1.0-rc.2.md) for that historical release and [current browser verification](verification.md) for the new app.
