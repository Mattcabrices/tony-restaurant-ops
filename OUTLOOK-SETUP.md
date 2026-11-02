# Outlook read-only setup

The source includes a Microsoft Graph Inbox adapter. Each restaurant supplies its
own administrator-approved registration and mailbox authorization.

1. Register a single-tenant Microsoft Entra application. Record the Application
   (client) ID and Directory (tenant) ID.
2. Configure it as a public client and enable public-client flows for device-code
   authentication. No client secret is needed.
3. Add Microsoft Graph **delegated Mail.Read**. Do not add application mailbox
   permissions or Mail.Send. Tenant policy may require administrator consent.
4. Run `python -m app.manage connect-outlook` locally, enter the two IDs, and
   complete Microsoft's displayed device sign-in in your own browser.
5. Run `python -m app.manage check-outlook`. This adapter reads bounded Inbox
   previews; sending, drafting and attachment ingestion are not implemented.

MSAL identity/offline scopes support sign-in and refresh. The token cache remains
in the operator's Windows DPAPI vault. Never publish tokens, device codes, caches
or secrets in GitHub issues or chat transcripts.

References: [Microsoft public-client setup](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration)
and [Graph Mail.Read](https://learn.microsoft.com/en-us/graph/permissions-reference#mailread).
