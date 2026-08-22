# Q4295: helpers/get-session-token — session id collision

## Question
Can an unprivileged attacker submit a session id derived from an attacker-chosen dest claim to `getSessionTokenHeader` in `helpers/get-session-token.ts` such that getJwtSessionId/getOfflineId maps a session id derived from an attacker-chosen dest claim to another shop's id, breaking the invariant that session id function is collision-free across shops, and leading to: cross-tenant access?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session id derived from an attacker-chosen dest claim
- Exploit idea: getJwtSessionId/getOfflineId maps a session id derived from an attacker-chosen dest claim to another shop's id
- Invariant to test: session id function is collision-free across shops
- Expected Immunefi impact: Cross-tenant access (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision search test
