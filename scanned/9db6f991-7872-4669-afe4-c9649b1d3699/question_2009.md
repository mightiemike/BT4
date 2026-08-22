# Q2009: session/decode-session-token — dest/iss shop confusion

## Question
Can an unprivileged attacker submit a JWT with duplicate or array-typed aud claims to `decodeSessionToken` in `session/decode-session-token.ts` such that session id from decodeSessionToken is derived from an attacker-chosen dest for a JWT with duplicate or array-typed aud claims, breaking the invariant that session id binds to verified shop only, and leading to: cross-tenant session takeover?

## Target
- File/function: `packages/apps/shopify-api/lib/session/decode-session-token.ts` -> `decodeSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with duplicate or array-typed aud claims
- Exploit idea: session id from decodeSessionToken is derived from an attacker-chosen dest for a JWT with duplicate or array-typed aud claims
- Invariant to test: session id binds to verified shop only
- Expected Immunefi impact: Cross-tenant session takeover (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert id changes only with valid dest
