# FlowBench verification and screenshots

[![Branch CI](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml/badge.svg?branch=feature%2Fsqlite-storage)](https://github.com/Super8Four/flowbench/actions/workflows/container.yaml?query=branch%3Afeature%2Fsqlite-storage) [![Prerelease](assets/badges/version.svg)](https://github.com/Super8Four/flowbench/releases/tag/v0.1.0-rc.1) [![Native images](assets/badges/platform.svg)](./setup-and-testing.md) [![Storage](assets/badges/storage.svg)](./sqlite-storage.md)

## Repeatable checks

Run `python -m unittest discover -s tests -v` with a verified iPerf3 engine. All 89 tests passed locally on Windows with Python 3.12 and the pinned 3.22 engine. The suite covers SQLite migration/rollback/import/export, simultaneous readers/capture/notes, actual lock contention/page exhaustion, denied writes, interrupted transactions/recovery, protocol job identity/outcomes, and managed listener raw-client captures, stop/resume/errors. Native Linux additionally executes UDP bidirectional; Windows checks its explicit rejection. Earlier Windows fixture-port allocation failures were corrected by probing random ports for both TCP and UDP; no host port reservations were changed.

The branch CI builds Ubuntu images on native AMD64 and ARM64 runners, runs the suite inside each image, then `scripts/check-container.py`. That script creates its own disposable container and volume, tests legacy migration, real TCP/UDP/Both, remote reports, JSON/XML round trips, managed incoming clients, independent overlap, resume without duplication, graceful incoming/outgoing stop and evidence/preset persistence after replacement. It verifies architecture, non-root/read-only runtime and SQLite integrity. No production Compose project/archive or live target is used.

Release publication requires terminal success for both native jobs at the exact source commit, followed by checked artifact hashes/OCI revision labels and verification that the release tag resolves to that commit. The live branch badge follows the named feature branch; it does not represent main or a stable release. The release page records the exact source and run.

## Browser QA

The UI was checked through the actual Edge browser with a disposable loopback fixture and synthetic records at CSS viewports 1440×1000, 375×843 and 843×375. Checks included wrapping, scrolling tables, three-test comparison, keyboard skip/sidebar navigation, listener validation/error-summary focus, missing remote evidence, empty search results and reduced-motion styling. Injected archive failure showed the error and disabled listener/outgoing controls; restoring access restored normal state. No application console errors were observed. Screenshots retain the supplied logo; private reference pictures are excluded. Browser captures below show the actual UI with synthetic measurements and reserved example endpoints. Browser-control timeouts occurred during some screenshot/navigation attempts; the listed checks were completed through the documented browser controls.

Targeted checks do not certify full WCAG compliance, close all UI/audit requirements, establish hardware durability/power-loss recovery or demonstrate a Pi deployment. Full/comparison charts draw measured samples directly and break paths across missing spans. History sparklines are bounded summaries with their own scale. Available controls/data remain limited to the implementation described in [requirements](requirements.md).

### Test and export

![Synthetic desktop test results](assets/test-desktop.jpg)

### Persistent history

![Synthetic desktop history](assets/history-desktop.jpg)

### Three-test comparison

![Synthetic three-test comparison](assets/comparison-desktop.jpg)

### Configuration and incoming capture

![Stopped managed listener configuration](assets/configuration-desktop.jpg)

### Mobile

![Synthetic mobile test results](assets/test-mobile.jpg)

![Mobile listener configuration](assets/configuration-mobile.jpg)
