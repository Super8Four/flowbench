# FlowBench brand assets

[![CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.2)

**Measure. Compare. Verify.**

| Asset | Use |
| --- | --- |
| [FlowBench SVG](../app/static/brand/flowbench-logo.svg) | Preferred scalable logo: 13 paths, outlined lettering, solid brand fills, transparent background |
| [Documentation SVG panel](assets/flowbench-logo-panel.svg) | Same vector paths on a light plaque for readable repository branding |
| [FlowBench PNG](../app/static/brand/flowbench-logo.png) | Original unchanged raster fallback |

Both assets use the same 2172×724 canvas and 3:1 aspect ratio. Preserve their proportions, colors and clear space. The dark artwork uses a light plaque in the dark application so the name remains legible. The app's `picture` source selects SVG in supporting browsers; the image retains PNG and `alt="FlowBench"`. Favicon links offer both formats.

The SVG is self-contained: no scripts, fonts, embedded images, CSS dependencies or external resources. It is served as `image/svg+xml`. The source PNG remains unchanged. The security/serving checks run in the application test suite and both native container builds.

0.1.0-rc.2 includes these assets in its source and rebuilt native image downloads. The existing rc.1 tag and downloads are preserved. See [release notes](release-0.1.0-rc.2.md) and [browser verification](verification.md).
