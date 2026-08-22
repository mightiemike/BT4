# Q1901: helpers/get-session-token — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a session id derived from an attacker-chosen dest claim to `getSessionTokenHeader` in `helpers/get-session-token.ts` such that session id from getSessionTokenHeader is derived from an attacker-chosen dest for a session id derived from an attacker-chosen dest claim, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session id derived from an attacker-chosen dest claim
- Exploit idea: session id from getSessionTokenHeader is derived from an attacker-chosen dest for a session id derived from an attacker-chosen dest claim
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
