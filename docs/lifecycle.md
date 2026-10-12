# Software lifecycle management

FlowBench changes move from a defined request to a feature branch, verified evidence and an explicitly authorized release. This guide describes the engineering process. It does not configure branch protection, require a new approval service or promise a support response time. The [testing map](testing.md) identifies the checks; [requirements](requirements.md) records acceptance boundaries.

## Ready to implement

A request is ready when its expected behavior, exclusions and acceptance examples are clear enough to test. Identify affected records, import/export formats, native tools and platforms before changing them. For measurement features, define units, missing-data behavior, comparability limits and cancellation/restart outcomes. Use the current remote source and inspect existing capabilities before deciding that a dependency or migration is needed.

Create a focused feature branch from freshly fetched remote `main` in an isolated checkout. Record the base SHA, read [repository guidance](../AGENTS.md), and preserve concurrent work. Reuse the user's existing authorization; feature implementation alone does not authorize a main merge, release or deployment.

## Done and reviewable

| Change | Evidence expected before handoff |
| --- | --- |
| Backend, native-tool adapter or API | Meaningful tests for measured output, validation, unavailable/error cases and owned-process cancellation; exact source and test result |
| Archive or import/export | Actual SQLite write/read and reopen checks, atomic failure behavior, old-record compatibility, portable exports and documented migration/rollback |
| UI or charts | Actual desktop/mobile interaction, keyboard focus, repeated/cancel/error flows, honest units and missing spans, sanitized screenshots |
| PDF or Word output | Model/redaction tests, generated fixtures, rendered pages inspected for clipping, pagination, labels and missing-data semantics |
| Documentation or dependency change | Working local links, accurate instructions/limits, reviewed pins/checksums and preserved applicable licenses/notices |

The handoff states what changed, why, the exact commit, passed/failed/skipped/unrun checks and remaining risks. Update [CHANGELOG.md](../CHANGELOG.md) and affected user guides for behavior changes. Do not call a mocked adapter test proof of native execution, a successful render proof of visual quality, or a passing container build proof of target deployment.

## Integration and release

The [current CI workflow](../.github/workflows/container.yaml) runs native Ubuntu AMD64/ARM64 builds, tests, disposable container lifecycle checks, optional actual Flent verification and report fixture rendering. Its configured triggers and artifact conditions are described in [testing](testing.md#ci-and-evidence). Repository policy recommendations in this guide are not enforced branch protection. Local Windows tests and interactive browser/render inspection also require recorded evidence.

For an authorized integration, resolve concurrent changes on the feature branch and rerun checks affected by that resolution. Verify terminal CI results for the exact integrated SHA. A main badge or a successful older run cannot substitute for that check. Keep unrelated fixes separate.

A prerelease requires an explicit publication instruction, an identified scope and terminal success for both native jobs at its source SHA. Verify the tag, archive checksums, image architecture and OCI source/version labels against that SHA. Release notes include install/upgrade/rollback links, platform limitations and unrun acceptance checks. Keep existing `v0.1.0-rc.1` and `v0.1.0-rc.2` tags and downloads immutable.

Stable promotion is a separate decision. Recommended gates are repeatable native results, reviewed Windows/browser/report evidence, a successful backup/restore rehearsal on disposable data, and explicit acceptance of remaining scope and platform limits. List open requirements and security boundaries; do not equate a version label with complete canonical acceptance. Authentication, retention, scenario thresholds and authenticated instance coordination remain tracked separately in [requirements](requirements.md).

Use [Semantic Versioning](https://semver.org/) for release identities: after a stable public API is declared, incompatible changes increase the major version, compatible features the minor version, and compatible fixes the patch version. Development versions in `0.y.z` do not promise a stable API. The branch currently uses `0.2.0-dev`; an authorized candidate may use an `-rc.N` suffix. Change the app version, changelog and package identity together for a release, and publish corrections as a new version rather than replacing released contents.

## Data and dependencies

Before upgrade or rollback, stop traffic and the app, then back up the complete archive to a new destination. Retain SQLite and any WAL/SHM sidecars together. Test migration and restore on a disposable copy, compare IDs/logs/metrics/project references and keep the original backup. Rollback uses a fresh compatible snapshot; older software may not understand new workflows. Never delete a database as a repair shortcut. Follow [SQLite storage](sqlite-storage.md) and [maintenance](install/maintenance.md).

For dependency changes, review the exact version/source, checksum where available, license, notices, runtime privileges and relevant upstream security advisories. Separate optional external tools from the portable application image; [Flent](flent.md) documents that boundary. Record any advisory scanner and its findings if used. Current CI has no dedicated vulnerability scan or coverage-percentage gate. A dependency upgrade does not authorize host installs, new capabilities, firewall changes or credential setup.

## Defects and maintenance

| Severity | Triage priority |
| --- | --- |
| Critical | Data loss/corruption, command execution from input or unintended access: preserve evidence, contain exposure and review affected versions privately |
| High | Lost capture, failed cancellation, unsafe traffic restart or broken migration: block affected release promotion until fixed or explicitly excluded with a safe workaround |
| Normal | Incorrect display/comparison/report behavior with preserved original evidence: reproduce, assess interpretation risk and add a regression test |
| Low | Copy, layout or documentation issue without misleading evidence: fix within the relevant scope and verify the changed surface |

Record a minimal sanitized reproduction, exact source/version, platform, expected/actual behavior and affected data. Keep secrets and real network/user archives out of public issues and fixtures. Fix the cause, retain a meaningful regression test and close the defect with exact verification evidence. Severity sets priority, not a response-time SLA.

Maintenance reviews are prompted by defects, dependency/security changes and requested releases. Recheck backup instructions, import compatibility, platform/tool limits and open requirements when those areas change. This guide creates no scheduled automation and authorizes no production maintenance or deployment.

## Measurement-workflows review record

The independent code review used main `d8d79f2c0382d04716be321b630eceee17149bf4` as its diff base and reviewed `5d2508295d5c33cc2ca69bc356f903e247e472a9`. It confirmed three normal-severity defects: imported direction text surviving report redaction ([#2](https://github.com/Super8Four/flowbench/issues/2)), Flent losing the selected IP family ([#5](https://github.com/Super8Four/flowbench/issues/5)), and zero-exit RRUL being marked successful without TCP load measurements ([#6](https://github.com/Super8Four/flowbench/issues/6)). Initial corrections are in `24a3f2afa54945eb3fa853c11009db8605bae99a`; follow-up checks also cover portable import claims and historical archive rows without rewriting original evidence. Regression tests are linked in the [testing map](testing.md).

Actual native Word rendering at `b1b5d5d0926d90530a861dfadbe15c642685c6ec` exposed lost legend entries across page boundaries ([#7](https://github.com/Super8Four/flowbench/issues/7)); fresh office conversion, text-retention checks and page inspection are required for that correction. Review of the proposed Windows workflow also caught an environment override that suppressed a deliberately tested disposable listener restart; it was removed before publishing the workflow.

Fix implementation and focused test results are distinct from final acceptance. Issue closure comments and the handoff record the exact delivered SHA, terminal native/Windows CI results and rendered-page review. This static record does not certify future changes or infer success from an artifact. Authentication ([#3](https://github.com/Super8Four/flowbench/issues/3)) and retention ([#4](https://github.com/Super8Four/flowbench/issues/4)) remain open acceptance gaps; physical Pi validation remains in [#1](https://github.com/Super8Four/flowbench/issues/1). These are separate from the confirmed defects above.
