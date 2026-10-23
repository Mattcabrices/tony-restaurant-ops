# Integration setup and honest status

The default demo is offline. Toast, Clover, Restaurant365, Outlook and Google Calendar names in the README describe an integration roadmap and explicitly labeled private implementation context, not ready-to-use public adapters. `contracts.py` demonstrates the shared observation schema; `pending()` returns `None` with `not_connected`, never a fictional zero.

## Optional monday.com checker

1. Obtain a token through your own monday.com account/admin's approved process. Prefer a narrowly scoped app credential where available. Personal tokens follow the user's account permissions and can carry more authority than this read-only checker uses.
2. Keep it in a local environment variable, never a tracked file, issue, screenshot or command argument. `.env.example` contains names only; the demo does not automatically load `.env`.
3. Run a no-network dry run using your board URL:

```bash
python -m tony_demo.integrations.monday --board https://example.monday.com/boards/123
```

4. Once the token is privately available as `MONDAY_API_TOKEN`, add `--live`. This prints real board titles to your local terminal; do not redirect that output into Git, screenshots or public artifacts.

```bash
python -m tony_demo.integrations.monday --board https://example.monday.com/boards/123 --live
```

The URL above is an illustrative placeholder. The checker returns at most 25 items and reports partial results. There is no automatic polling, scheduler, mutation, file download, update parsing, token storage, or board modification.

The version is explicitly pinned to `2026-07`, a supported stable version documented at implementation time. Review provider versioning before its retirement. [Official request guide](https://developer.monday.com/api-reference/docs/getting-started) · [Versioning](https://developer.monday.com/api-reference/docs/api-versioning) · [Authentication](https://developer.monday.com/api-reference/docs/authentication).

## Google Calendar versus Google Sheets

Google Calendar's API and a spreadsheet containing calendar notes are different integrations. The private source app currently imports monthly Google Sheets tabs; direct Calendar OAuth/ICS/API work is pending. This public edition includes neither live connection. Never advertise a spreadsheet import as a verified Calendar API connection.

## Coming soon

Restaurant365 invoices/inventory/costs, Outlook read authorization/attachments, direct Calendar reads and additional vendor adapters require genuine account access, permission review, documented metric semantics, bounded requests and synthetic contract tests. Sending mail, writing events, modifying boards, purchasing and accounting writes require separate approval and post-action verification design.
