# Q2070: helpers/validate-session-token — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a token whose sub encodes another user's id to `validateSessionToken` in `helpers/validate-session-token.ts` such that session id from validateSessionToken is derived from an attacker-chosen dest for a token whose sub encodes another user's id, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose sub encodes another user's id
- Exploit idea: session id from validateSessionToken is derived from an attacker-chosen dest for a token whose sub encodes another user's id
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
