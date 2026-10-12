# FlowBench 0.1.0-rc.2

[![Main CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=main)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Amain) [![Prerelease](https://img.shields.io/github/v/release/Super8Four/flowbench?include_prereleases=true&sort=semver&label=prerelease&color=183b50&cacheSeconds=300)](https://github.com/Super8Four/flowbench/releases) [![Container](assets/badges/container.svg)](setup-and-testing.md) [![Architecture](assets/badges/platform.svg)](setup-and-testing.md) [![Python](assets/badges/python.svg)](setup-and-testing.md) [![SQLite](assets/badges/storage.svg)](sqlite-storage.md) [![iPerf3](assets/badges/engine.svg)](protocol-tests.md)

**SVG logo update · Measure. Compare. Verify.**

The sidebar and favicon now prefer the supplied true vector SVG. Lettering is outlined and the artwork has no scripts, embedded raster, fonts or external resources. The original PNG fallback, colors, aspect ratio, accessible name and light plaque remain. [Brand assets](brand-assets.md) explains reuse; [verification](verification.md) includes actual app screenshots with synthetic data.

README branding and screenshots now use the vector artwork. The UI and product documentation have shorter copy, without framework credits, repeated slogans or development-process notes. Third-party licenses and attribution remain in their notice files.

Native AMD64 and ARM64 images are rebuilt for this version. CI derives package names and OCI version labels from `app/version.py`, checks the app/version and both logo resources inside the running image, and runs the complete regression/lifecycle suite. Publication requires terminal success at the exact final main commit, checked image source/version/architecture/config identities and asset checksums. The release page records that commit and run.

Use the matching architecture's `.tar.gz`, `.image.json` and `.SHA256SUMS` files following [installation](setup-and-testing.md). GitHub source downloads contain the same SVG and PNG. Compatibility image tag remains `iperf-workspace:local`.

This update makes no database schema or evidence-format change. Retain and back up the existing data volume before replacing the image. [SQLite migration and rollback](sqlite-storage.md) remain applicable.

The rc.1 release, tag and assets remain unchanged. This is another prerelease, not a stable release or Pi deployment. Authentication and R13–R17 remain pending; keep the API within an approved trusted access boundary. Broader requirements/WCAG/audit acceptance is not implied.
