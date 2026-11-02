# Validation record

## Open-source operational alpha — November 2, 2026

- 159 public tests passed locally on Windows/Python 3.14.7, using synthetic state.
- Full application source parses, dashboard JavaScript syntax passes, and the public-tree guard passes.
- Source scanning found no known private menu/account identifiers or curated original restaurant context.
- Generic coaching PDF rendered with existing optional dependencies; fictional labels appeared on all pages. No package was installed.
- Native Windows AppContainer: all 159 tests and five denial probes passed (private decoy read, outside write, internet, loopback and child process). Source fingerprint: `4030f12fbb6b0c9f732187a1daef93cdc6d483abf2d3bcae1fd7f29954eb8d9b`. No production vault exists in this public checkout; that private-vault probe was not claimed for this edition.
- No real vendor credentials, mailboxes, Telegram recipients or production data were used for public-edition testing.
- Fresh Windows VM installation and new remote CI are not yet established for this alpha. Earlier private deployment counts are not this public edition's validation.

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
