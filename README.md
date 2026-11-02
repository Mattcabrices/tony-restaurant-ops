# Tony — Open-Source Restaurant Operations Assistant

A self-hosted restaurant assistant with natural-language reporting, Telegram,
persistent operational memory, historical analysis, a research workspace and
human-approved task workflows. **MIT licensed. Windows operational alpha.**

Built by [Matt Cabrices](https://github.com/Mattcabrices), with AI-assisted development.
The operational source is in `app/`; the portable, offline portfolio example
remains in `tony_demo/`. This repository contains reusable code, not a restaurant's
records, recipes, employee information, account IDs or credentials.

## Install the operational application

Use **Windows 10/11 x64 and Python 3.14.7**. Clone this repository and run from its directory:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-runtime-win.lock
.\.venv\Scripts\python.exe -m app.manage serve
```

Open [Tony locally](http://127.0.0.1:8790). In another local terminal, run
`.\.venv\Scripts\python.exe -m app.manage setup-code` to obtain your first-run
setup code, then choose your dashboard password. Demo mode is on until you
configure your own sources. **Do not reuse another restaurant's vault or data.**

In Settings, choose **Continue with ChatGPT** and authorize eligible ChatGPT plan
usage. No OpenAI API key or automatic API billing fallback is configured.
OpenAI supports eligible Plus/Pro plan usage in open-source clients; eligibility,
models, features and limits remain controlled by OpenAI. Each person connects
their own account. Plus usage is shared with their other apps, not an unlimited
separate allowance. Leave app credit spending disabled if you want included
usage only. [Official sign-in documentation](https://developers.openai.com/siwc/quickstart)
and [shared usage limits](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions).

ChatGPT does not supply POS credentials, vendor API permissions, a computer,
email administration or paid vendor services. See [installation and configuration](docs/SELF-HOSTING.md).

## Included source and actual integration status

| Component | Included implementation | What the installer must provide |
|---|---|---|
| Toast | Read-only sales, checks, items, modifiers, recorded labor, guest/tab lookup and future bookings | Approved API credentials and scopes; own menu identity mappings; actual source coverage |
| Clover | Read-only client and historical aggregate importer | Merchant credentials and permitted historical access/imports |
| Telegram | Allowlisted private chat, commands, optional briefing delivery, guest/coaching flows and approval routes | Own bot token, private user/chat IDs, explicit delivery choices |
| Outlook | Delegated Graph `Mail.Read` sign-in and bounded Inbox previews | Own Microsoft Entra public-client registration and tenant consent; no sending or drafting implemented |
| monday.com | Bounded read adapter and manual Excel import/weekly refresh workflow | Own board/token or exports; no board writes |
| Event calendar | Google Sheets monthly-tab event importer and local snapshots | Readable shared Sheet; **direct Google Calendar API is not implemented** |
| Restaurant365 | Typed invoice, inventory, recipe-cost, purchasing, vendor and accounting provider interfaces | **Live R365 connector is pending implementation and vendor access** |
| ezCater | Signed receipt validation | Full order hydration and hosted delivery are pending; not a complete connector |
| Research | SQLite journals, hypotheses, predictions, scorecards, chronological baselines/backtests, provenance and bounded public connectors | Own relevant dates, source URLs, location and explicit budgets/schedule approval |
| Coaching | Private narrative intake, evidence validation, follow-up revision and generic unsigned PDF renderer | Optional PDF dependencies; manager/HR review; no proprietary employer form supplied |
| Approvals | Deterministic task proposals and exact-diff/hash coding approvals with native isolated validation | Explicit owner approval; optional installed Codex CLI for patch generation; no unrestricted execution fallback |

A connector implementation is not proof that an account is connected or that
all vendor endpoints are available. Missing data is never zero. Toast net sales
and Clover gross line sales stay separate; labor coverage must be reconciled.

## Restaurant-specific configuration

Copy `examples/restaurant.example.json` to **ignored** `data/restaurant.json` and
configure display name, optional public-search location, approved public hosts,
confirmed pieces per menu portion, company values and public research topics.
Set closeout and reporting week in Settings. This first operational release uses
**America/New_York** for business dates and schedules; other zones need adaptation.

Whole-pizza mapping and serving builds are supplied by each restaurant, never
borrowed from the original deployment. See [self-hosting](docs/SELF-HOSTING.md).
Research schedules, automatic AI narratives and Telegram briefings are off by
default. Nothing is installed into Task Scheduler by cloning or launching Tony.

## Security and operation

Local dashboard authentication, CSRF/host checks, bounded HTTP bodies, no-proxy
credentialed clients, Windows DPAPI/ACL storage and mandatory instance locking
are included. Research experiments and generated patches use restricted Windows
AppContainers and fail closed without isolation. Chat cannot authorize its own
patches, task execution, emails or external writes. External text is untrusted data.

Keep the server bound to loopback. Do not expose the dashboard directly to the
public internet. Review [security](SECURITY.md), [agent instructions](AGENTS.md)
and [release validation](docs/VALIDATION.md) before deployment. This is an alpha,
not a managed hosting service or a promise of vendor completeness.

## Tests and dry runs

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_public_bundle.py
.\.venv\Scripts\python.exe -m app.corner_cli dry-run
.\.venv\Scripts\python.exe -m scripts.validate_code_isolation --confirm
```

The final command explicitly creates disposable Windows isolation/ACL resources;
it does not install packages or promote a patch. Native isolation and a fresh-VM
installation are distinct from unit tests. See the validation record for what
was actually run on this edition.

## Portable offline example

The original synthetic example still runs on Windows/Linux with `requirements.lock`:
`python -m tony_demo summary`, or
`python -m uvicorn tony_demo.web:create_app --factory --host 127.0.0.1 --port 8789`.
Its invented fixtures and forecast scores are not real restaurant results.

MIT applies to this project's original code. Third-party packages retain their
own licenses. Vendor names do not imply endorsement. No production state,
original employer forms or proprietary build books are distributed.
