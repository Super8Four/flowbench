# FlowBench project guidance

For FlowBench UI/UX work, read `.agents/skills/ui-ux-pro-max/SKILL.md` and the relevant references. The project-scoped copy is pinned to nextlevelbuilder/ui-ux-pro-max-skill commit `7246805992a42b7bba37d4cac83b01416f0c6c44`; its MIT notice is preserved beside the skill. Do not run its package installer or update it implicitly.

User-approved design: FlowBench, "Measure. Compare. Verify.", gunmetal/deep navy/crimson, page `#1b1f22`, cards `#212629`, borders `#41494d`, text `#e3edf5`, muted `#aab6bf`. Preserve the supplied logo, dynamic measured values and green persistent-history badge. Recommendations that conflict with these decisions are advisory.

Keep the UI offline-capable, use semantic controls and visible focus, preserve saved choices, and show unavailable/failed/interrupted evidence honestly. Test changed flows with synthetic fixtures on desktop and mobile. Never publish private visual references, endpoints, credentials or user archives as screenshots or release assets.

Use disposable archives and bounded loopback/container peers for tests. Do not change live services, network/security settings, another checkout, private vault content or main without separate user authorization. Document remaining acceptance gaps; a passing image build does not establish Pi deployment or complete requirements acceptance.
