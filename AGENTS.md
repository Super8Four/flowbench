# FlowBench project guidance

For FlowBench UI/UX work, read `.agents/skills/ui-ux-pro-max/SKILL.md` and the relevant references. The project-scoped copy is pinned to nextlevelbuilder/ui-ux-pro-max-skill commit `7246805992a42b7bba37d4cac83b01416f0c6c44`; its MIT notice is preserved beside the skill. Do not run its package installer or update it implicitly.

User-approved design: FlowBench, "Measure. Compare. Verify.", gunmetal/deep navy/crimson, page `#1b1f22`, cards `#212629`, borders `#41494d`, text `#e3edf5`, muted `#aab6bf`. Preserve the supplied logo, dynamic measured values and green persistent-history badge. Recommendations that conflict with these decisions are advisory.

Keep the UI offline-capable, use semantic controls and visible focus, preserve saved choices, and show unavailable/failed/interrupted evidence honestly. Test changed flows with synthetic fixtures on desktop and mobile. Never publish private visual references, endpoints, credentials or user archives as screenshots or release assets.

Use disposable archives and bounded loopback/container peers for tests. Do not change live services, network/security settings, another checkout, private vault content or main without separate user authorization. Document remaining acceptance gaps; a passing image build does not establish Pi deployment or complete requirements acceptance.

## Engineering process

Follow [lifecycle guidance](docs/lifecycle.md) and the [testing map](docs/testing.md). Start new feature work from freshly fetched remote main in an isolated branch, inspect actual architecture and preserve concurrent work. Keep the request's scope and existing session authorization; do not infer a merge, release or deployment from feature implementation alone.

For meaningful behavior changes, add focused regression tests for real failure/compatibility risks and run the relevant checks. Record the exact source SHA with Windows and native AMD64/ARM64 CI results, including failures/skips/unrun checks. Check changed UI in the actual browser on desktop/mobile with keyboard/error/cancel flows; render changed PDF/DOCX fixtures and inspect their pages. Use sanitized disposable fixtures and keep private evidence out of public artifacts.

Update affected guides and the changelog for behavior changes. Preserve SQLite backup/migration/rollback compatibility and applicable dependency notices. Published tags/assets remain immutable. Recommended lifecycle gates do not imply branch protection, a new approval mechanism, complete requirements acceptance or a support SLA.
