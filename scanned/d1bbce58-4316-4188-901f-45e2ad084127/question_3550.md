# Q3550: session/session — url-param token trust

## Question
Can an unprivileged attacker submit a bearer token placed in the URL param instead of the header to `equals` in `session/session.ts` such that getSessionTokenFromUrlParam trusts a bearer token placed in the URL param instead of the header equally to the header, breaking the invariant that token source does not weaken verification, and leading to: token injection via url?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `equals`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a bearer token placed in the URL param instead of the header
- Exploit idea: getSessionTokenFromUrlParam trusts a bearer token placed in the URL param instead of the header equally to the header
- Invariant to test: token source does not weaken verification
- Expected Immunefi impact: Token injection via URL (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: submit token in query, assert same strict verify
