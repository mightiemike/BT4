# Q2125: session/session — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a session token header with unexpected casing/whitespace to `toObject` in `session/session.ts` such that session id from toObject is derived from an attacker-chosen dest for a session token header with unexpected casing/whitespace, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `toObject`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session token header with unexpected casing/whitespace
- Exploit idea: session id from toObject is derived from an attacker-chosen dest for a session token header with unexpected casing/whitespace
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
