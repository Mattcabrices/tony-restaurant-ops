# Release notes

## November 2, 2026 — private lookup reliability release

This public update documents the latest private application deployment. It does
not add live connectors or the private lookup implementation to the demo.

### Private application changes

- Unified read-only actuals retrieval across imported Clover aggregates, stored
  Toast observations and bounded live Toast recovery. Source-specific coverage,
  missing dates and freshness accompany the answer. Clover gross line sales and
  Toast net sales remain separate metrics.
- Persistent booking references retain only order identifiers, fulfillment dates
  and observation timestamps. Fresh reads detect changed dates and cancellations.
  Queries can target up to 366 business dates ahead. This index remembers observed
  bookings; it cannot guarantee discovery of old unchanged orders never returned
  by a source read.
- Repeated identical tool requests execute once per answer. Distinct reads,
  model rounds, HTTP pagination and time remain bounded. An unavailable narrative
  preserves retrieved facts rather than substituting an unrelated overview.
- Synthetic conversation and data-quality tests cover historical questions,
  follow-ups, missing dates, mixed metrics, unknown piece sizes, booking moves and
  cancellations, and source failures.

### Validation and scope

Private deployment validation passed 489 regression tests, 488 sandbox-compatible
tests and six Windows AppContainer denial probes. One real historical-sales
question returned an AI-written answer matching its imported source aggregate.
These private results are not the public demo's test count, proof of every natural
language phrasing, or proof of vendor completeness. They do not establish a clean
VM or remote CI run for the private deployment.

Existing communication channels and schedules were preserved. No new AI
subscription or scheduled model work was added. Outlook and Restaurant365 remain
dependent on account authorization; this release does not activate them.

This public commit contains documentation only. It includes no production code
bundle, credentials, account identifiers, operational records, recipes or private
communications. See [public-demo validation](VALIDATION.md) for checks performed
on this repository.
