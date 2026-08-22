# Q1840: session/session — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a JWT with a leeway-abusing exp just outside clock skew to `Session` in `session/session.ts` such that session id from Session is derived from an attacker-chosen dest for a JWT with a leeway-abusing exp just outside clock skew, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `Session`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a leeway-abusing exp just outside clock skew
- Exploit idea: session id from Session is derived from an attacker-chosen dest for a JWT with a leeway-abusing exp just outside clock skew
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
