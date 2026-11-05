# Validation record

## Open-source operational alpha — November 2, 2026

- 165 public tests passed locally on Windows/Python 3.14.7, using synthetic state.
- Full application source parses, dashboard JavaScript syntax passes, and the public-tree guard passes.
- Source scanning found no known private menu/account identifiers or curated original restaurant context.
- Generic coaching PDF rendered with existing optional dependencies; fictional labels appeared on all pages. No package was installed.
- Native Windows AppContainer: all 165 tests and five denial probes passed (private decoy read, outside write, internet, loopback and child process). Source fingerprint: `76ba4a9ddd17aef5b2e7c8c1ae2bf1d5b377958c03c6e51013c364ce4c83c63d`. No production vault exists in this public checkout; that private-vault probe was not claimed for this edition.
- No real vendor credentials, mailboxes, Telegram recipients or production data were used for public-edition testing.
- Fresh Windows VM installation remains untested. The initial operational CI passed on Windows and Linux ([run](https://github.com/Mattcabrices/tony-restaurant-ops/actions/runs/41538788305)); subsequent source changes require their own checks. Earlier private deployment counts are not this public edition's validation.
- Synthetic channel-window tests cover dine-in, in-store takeout, explicitly configured online takeout using API order source, refunds, duplicates, unknown classifications, missing timestamps, pagination limits and natural-language tool execution. Recall tests enforce channel separation and exclusion of private guest/coaching archives.

## November 2, 2026 documentation update

The public README and release notes now describe the private lookup reliability
release while explicitly distinguishing it from the synthetic demo. All **21
public tests passed** locally, and the public-tree guard passed. No runtime code,
dependencies or fixtures changed. The earlier hosted CI runs below apply to
their respective commits; a new remote CI result is not yet claimed here.

## Original public demo validation

Validated locally on Windows x64 with Python 3.14.7 on October 23, 2026:

- **21 tests passed** for this public edition.
- CLI summary reproduced the documented synthetic values.
- JavaScript syntax check passed.
- Local FastAPI routes and synthetic-data markers passed; no public write API is exposed.
- Browser inspection confirmed the dashboard's fixed business date, source marker, sample order totals, forecast range, integration status table, and the distinction between coming-soon and implemented components.
- Public-tree guard passed. A separate pre-publication check found no known live credential values or private restaurant identifiers in the selected public files.

The public snapshot was assembled from an explicit source/document allowlist. No existing production Git history, databases, logs, account IDs, private serving-build overrides or source exports are included. A new repository history is used.

Remote CI subsequently passed on both Windows and Linux using Python 3.14.7, a fresh hashed dependency installation, dependency consistency checks, all 21 tests, the public-tree guard, the public dependency vulnerability check and JavaScript syntax validation. [Verified CI run](https://github.com/Mattcabrices/tony-restaurant-ops/actions/runs/40007115981) and [screenshot-inclusive CI run](https://github.com/Mattcabrices/tony-restaurant-ops/actions/runs/40007443674).

The first workflow attempt caught a YAML quoting error before job execution; it was corrected and the passing runs above validate the fix. A fresh clone of the published repository also passed all 21 local tests and the source guard. Every Git-history blob was checked against known live credential values with zero matches.

No real credentialed vendor read was performed for this public demo. The optional monday.com checker passed mocked tests; live activation remains token-dependent. These public hosted-runner checks do not establish production Windows isolation, private account access, or real forecasting accuracy.

Forecast/backtest scores are computed from a deliberately fabricated 120-day series. They are not a measured accuracy claim for any real restaurant.

## November 4, 2026 — staged amount-based order lookup

All 169 public tests passed in the offline Windows AppContainer with five denial
probes. Exact source fingerprint:
`ddbdacd36ac16c373ecb07d105a7f97589d51511e176b941cd6b5113f6cead19`.
The isolated runner now supplies its own pytest configuration explicitly, avoiding
configuration discovery outside the disposable source tree. No host ACL was relaxed.
Amount search tests use invented receipt data, inclusive boundaries, split checks,
refund distinctions, invalid/missing amounts, request/result/output caps and AI tool
execution. Public guard passed. These changes have not yet been published or run
in remote CI; older hosted results above apply only to their respective commits.

## November 4, 2026 — staged operator workspace

All 181 public regression cases passed in offline native Windows isolation with
five denial probes. Source fingerprint:
`03979f48c7e012a5ea3c4c5bb92e0250de148a7feb25f2cb8943f6a8e45cc77b`.
Uses only invented test records. Public-tree guard and both dashboard JavaScript
syntax checks passed. New source includes typed operator HTTP/chat adapters,
receipt lookup, source-safe comparisons, explicit current-day observed totals,
bounded metadata audit, and an on-demand forecast broker using the existing jail.
Real restaurant validation data and private forecasts were not copied here.
These source changes are local and have not yet been published or run in remote CI.
