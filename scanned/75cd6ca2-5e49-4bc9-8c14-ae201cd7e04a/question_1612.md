# Q1612: session/session — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a JWT signed with alg=none or an unexpected alg header to `isExpired` in `session/session.ts` such that session id from isExpired is derived from an attacker-chosen dest for a JWT signed with alg=none or an unexpected alg header, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `isExpired`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT signed with alg=none or an unexpected alg header
- Exploit idea: session id from isExpired is derived from an attacker-chosen dest for a JWT signed with alg=none or an unexpected alg header
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
