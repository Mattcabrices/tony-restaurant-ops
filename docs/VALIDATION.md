# Validation record

Validated locally on Windows x64 with Python 3.14.7 on October 23, 2026:

- **21 tests passed** for this public edition.
- CLI summary reproduced the documented synthetic values.
- JavaScript syntax check passed.
- Local FastAPI routes and synthetic-data markers passed; no public write API is exposed.
- Browser inspection confirmed the dashboard's fixed business date, source marker, sample order totals, forecast range, integration status table, and the distinction between coming-soon and implemented components.
- Public-tree guard passed. A separate pre-publication check found no known live credential values or private restaurant identifiers in the selected public files.

The public snapshot was assembled from an explicit source/document allowlist. No existing production Git history, databases, logs, account IDs, private serving-build overrides or source exports are included. A new repository history is used.

CI is configured for Windows and Linux with Python 3.14.7 and a hashed dependency lock. Remote workflow outcomes must be checked on GitHub; configuration is not a passing result. No clean-machine installation or real credentialed vendor read was performed for this public demo. The optional monday.com checker passed mocked tests; live activation remains token-dependent.

Forecast/backtest scores are computed from a deliberately fabricated 120-day series. They are not a measured accuracy claim for any real restaurant.
