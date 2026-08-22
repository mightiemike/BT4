# Q2242: helpers/get-session-token-header — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a token whose iss host differs from dest host to `getSessionTokenFromUrlParam` in `helpers/get-session-token-header.ts` such that session id from getSessionTokenFromUrlParam is derived from an attacker-chosen dest for a token whose iss host differs from dest host, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenFromUrlParam`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose iss host differs from dest host
- Exploit idea: session id from getSessionTokenFromUrlParam is derived from an attacker-chosen dest for a token whose iss host differs from dest host
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
