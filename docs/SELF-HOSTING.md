# Self-hosting Tony (Windows alpha)

## First installation

Follow the README's Python 3.14.7 environment and hashed Windows dependency
installation. Use a dedicated copy of the repository under your Windows account.
Start `python -m app.manage serve`, open `http://127.0.0.1:8790`, and get the
one-time setup code with `python -m app.manage setup-code` in another terminal.
Choose a strong dashboard password. Secrets are owner/SYSTEM ACL protected and
DPAPI-encrypted for that Windows account. Do not copy a vault between users.

The `TONY_PORT` and `TONY_ORIGIN` environment settings control the local listener
and origin checks. Keep defaults for the first installation. `TONY_DATA_DIR`
can relocate operations state, but the serving-build/profile/mapping paths and
Corner CLI currently use repository-local `data/`; use the default data layout
for a consistent first release. Do not point any setting at another deployment.

## Connect accounts

- ChatGPT: Settings → Continue with ChatGPT. The actual app name is Tony Restaurant
  Ops. Authorize your own eligible plan; models and features come from OpenAI's
  returned capabilities. No shared subscription or separate API key is supplied.
- Toast: `python -m app.manage configure`, choose toast. Enter the owner-provided
  client ID, client secret, restaurant GUID and approved API host. Enable live
  mode only after checking a read. Guest lookups need the vendor's guest scope.
- Telegram: the same configure command, choose telegram. Enter your own BotFather
  token, numeric user ID and private chat ID. Enable briefing delivery separately
  in Settings. Groups and arbitrary recipients are not supported.
- Outlook: create your own Entra public-client app; follow `OUTLOOK-SETUP.md` and
  run `python -m app.manage connect-outlook`. No client secret, Mail.Send or
  application-wide unattended mailbox permissions are requested.
- monday.com: Settings → Connect monday.com with your own board link/token; or
  import your own Excel export. Tokens can have broader permissions than the
  fixed read query: restrict access in monday.com where possible.
- Events: Settings → connect your readable Google Sheet with month tabs. This
  is a Sheet importer, not a direct Google Calendar OAuth connector.
- R365: the interfaces in `app/providers.py` are included for development. A real
  read adapter, endpoint/auth verification and metric reconciliation are still
  required. Entering credentials alone cannot activate an interface.

Each vendor's access requirements and charges are independent of ChatGPT Plus.

## Restaurant knowledge and quantities

Copy `examples/restaurant.example.json` to ignored `data/restaurant.json`. Public
location and research topics are deliberately empty; entering them authorizes
those strings to be used in public-source queries when search is enabled. No
saved restaurant records or chat history accompany public searches.

Set closeout, reporting week and overtime assumptions in Settings. Missing labor
systems make labor partial. All dates/schedules currently use America/New_York.

Copy `examples/toast_pizza_mapping.example.json` to ignored
`data/toast_pizza_mapping.json`; replace placeholder keys with your real Toast
item/group GUIDs, review classifications and date/version the map. Missing or
ambiguous mapping withholds exact whole-pizza counts. GUIDs must never enter Git.
`pieces_per_portion` supplies only confirmed exact lower-case menu labels; no
restaurant's kids-meal count is assumed.

Standard builds can be imported as owner-authored JSON:
`python -m scripts.import_line_builds YOUR_PRIVATE_JSON --confirm`. Input has
`source_title` and `builds`; each build supplies name, aliases, source_section,
components (or sizes) and notes. This is not the original employer's recipe parser.
Keep all input and compiled catalogs out of Git.

## Coaching PDFs

Narrative drafting/revision works with the ChatGPT connection. To enable the
included generic unsigned PDF layout, explicitly install the optional pinned
local dependencies: `python -m pip install --require-hashes --only-binary=:all:
-r requirements-pdf.lock` (one command). No package is installed automatically.
The public renderer uses the current virtual environment, not a Codex cache.
The proprietary employer form and example employee documents are not included.
Unknowns, potential impact and proposals stay labeled; manager/HR review is required.

## Background work and approvals

No startup task is installed automatically. Optionally run `Install-Startup.ps1`
as an explicit operator choice; it registers `TonyRestaurantOps-LocalOperations`
for the current signed-in account, distinct from other deployments. Use
`python -m app.manage start|stop|restart|status` after installing that task; otherwise
run serve in a terminal and stop with Ctrl+C.

Research schedules/model/search budgets start disabled. Inspect
`python -m app.corner_cli --help` and dry-run first. Native research isolation is
an explicit setup step. Only approve a bounded schedule after configuring your
own sources, dates and budgets. Weekly Sheet/Excel refresh also requires approval;
it does not download monday.com exports from a browser automatically.

The sports collectors include NFL/FBS feeds and the original Bills/UGA feature
families as optional algorithms, not claims about your restaurant's audience.
No offline sports archive is shipped. `python -m scripts.football_import` is an
explicit network/import operation for its documented seasonal window. Other
teams/seasons need an adapted, source-attributed collector; do not invent dates.

The public-page task starts with no approved hosts. Add explicit public hostnames
in the profile and restart. Existing public-IP/HTTPS/size/redirect checks remain.
The approved code workflow optionally uses an installed Codex CLI for proposal
creation. Approving a hash is separate from generating it. Protected files and
unavailable native isolation fail closed; there is no unrestricted test fallback.

## Backups and upgrades

Back up through authenticated Settings or `python -m app.manage backup`. Keep
backups private and encrypted storage protected. Stop Tony before a source upgrade,
retain the prior commit and a current state backup, review migrations, then start
and check health and configured integrations. Do not replace current data with an
old backup merely to roll back source. Production data is never a test fixture.

Support scope: source-level Windows alpha, tested locally with synthetic data.
A fresh Windows VM installation, other operating systems, vendor-account activation
and each restaurant's permissions/data quality require independent verification.
