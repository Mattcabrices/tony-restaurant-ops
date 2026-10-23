# Validation record

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
