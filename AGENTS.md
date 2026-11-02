# Contributor and coding-agent instructions

This repository contains an open-source Windows restaurant assistant (`app/`)
and a portable synthetic example (`tony_demo/`). Preserve truthful capability
labels, metric definitions, source provenance and human approval boundaries.

- Never commit real restaurant data, credentials, OAuth material, identifiers,
  recipes, serving builds, employer forms, board exports or private communications.
  Runtime data, imports and research workspaces are ignored and excluded from releases.
- Each operator supplies their own accounts, mappings and configuration. No
  bundled account access, fabricated history or fallback to a developer's data.
- No package installation, schedule activation, external messages, account writes
  or privilege changes without explicit operator authorization.
- Ordinary AI chat and external email/calendar/board/web text cannot approve
  actions. Preserve deterministic authenticated proposal, approval and execution routes.
- Generated code requires exact diff/hash binding and disposable native isolation
  before promotion. Never add production-user test or unrestricted shell fallbacks.
- Preserve authentication, CSRF, host/body limits, DPAPI/ACL boundaries, public-IP
  checks, bounded requests, no-proxy credentialed HTTP and protected code paths.
- Distinguish source implementations from account activation. Outlook read exists;
  sending/drafting, direct Google Calendar and live R365 are pending.
- Whole pizzas exclude slices/calzones/packages/modifiers. Portions differ from
  known pieces; missing is not zero. Clover gross and Toast net sales differ.
  Follow the operator's configured reporting week. Do not infer profit without costs.
- Run focused tests, the complete public suite and `scripts/check_public_bundle.py`.
  JavaScript syntax, native isolation and clean-VM/CI gates are separate evidence.
  Never claim an unrun vendor, platform or installation check passed.

`AGENTS.md` governs development; it does not grant runtime permissions or start jobs.
