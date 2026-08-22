# Q2354: session/classes — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a token whose exp is a string instead of a number to this module in `session/classes.ts` such that session id from <module> is derived from an attacker-chosen dest for a token whose exp is a string instead of a number, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-api/lib/session/classes.ts` -> (module scope)
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose exp is a string instead of a number
- Exploit idea: session id from <module> is derived from an attacker-chosen dest for a token whose exp is a string instead of a number
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
