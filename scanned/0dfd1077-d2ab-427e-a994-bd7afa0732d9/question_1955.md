# Q1955: session/classes — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a bearer token placed in the URL param instead of the header to this module in `session/classes.ts` such that session id from <module> is derived from an attacker-chosen dest for a bearer token placed in the URL param instead of the header, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-api/lib/session/classes.ts` -> (module scope)
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a bearer token placed in the URL param instead of the header
- Exploit idea: session id from <module> is derived from an attacker-chosen dest for a bearer token placed in the URL param instead of the header
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
