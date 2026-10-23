# Architecture and scope

The private application is a restaurant-specific local operations engine. This repository extracts a small, independently runnable subset, rather than attempting to copy its state or replace its security-sensitive deployment.

Reporting and forecast routines were selected from existing modules. Private restaurant overrides, identities, schedules, source mappings, and business history were omitted. The exception ledger was extracted with a synthetic-only SQLite store. The dashboard, fixture generator, CLI, public contracts and isolated monday.com checker are portfolio presentation components.

## Trust boundaries

- Fixtures are fabricated in code. Neither CLI summary nor web startup reads an environment token, calls an AI service or queries a real account.
- Domain calculations do not perform network or model calls.
- The web app exposes only read endpoints and static files. It has no production authentication, live-connector endpoint, write API, code runner or persistent memory ingestion.
- Optional monday.com reads are a separate CLI path requiring `--live` and an environment token. They are not available to web text or an AI tool loop.
- External text never supplies executable instructions or approval.

## Forecast interpretation

Same-weekday medians use bounded trailing history with explicit horizon cutoffs. Rolling backtests score predictions against later actuals without feeding those target values into training. Empirical residual ranges do not guarantee future coverage. Revised historical observations are not original as-known data vintages. The synthetic series exists to verify mechanics, not to advertise accuracy or financial gains.

## Private deployment capabilities intentionally omitted

Actual Toast/Clover reads, Microsoft authentication, encrypted token storage, private Telegram messaging, research scheduling, Windows sandbox execution and hash-approved source promotion are not published here. Their existence in a private deployment does not make the public app production-ready. No restaurant data or account access is distributed.
