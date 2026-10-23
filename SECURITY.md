# Security policy

This is an offline-by-default synthetic demonstration, not a hardened public multi-user service. Bind the demo to `127.0.0.1`. Do not connect production data or expose it publicly without a separate authentication and deployment review.

The monday.com checker runs only through an explicit CLI `--live` command. It uses one fixed read query against the official HTTPS API, disables environment proxies/redirects, bounds its returned items, and does not save tokens. Board text is untrusted. The token itself may have broader privileges; the code's read boundary does not reduce those permissions at the provider.

Never commit `.env`, tokens, database files, raw exports, session cookies, account IDs, private menus, operational notes, or real customer/employee data. The public-bundle checker is an additional guard, not proof that a secret or sensitive detail cannot exist. Review every staged diff and the full Git history before publication. Rotate exposed credentials even if a commit is removed.

Report security issues privately to the repository owner through GitHub's private reporting feature when available. Do not put credentials or private business records in public issues. Automated dependency scanning covers the pinned dependency list, not every component of a developer's machine.
