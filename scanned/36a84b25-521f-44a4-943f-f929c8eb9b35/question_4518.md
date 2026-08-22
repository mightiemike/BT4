# Q4518: session/session-utils — session id collision

## Question
Can an unprivileged attacker submit a session token header with unexpected casing/whitespace to `customAppSession` in `session/session-utils.ts` such that getJwtSessionId/getOfflineId maps a session token header with unexpected casing/whitespace to another shop's id, breaking the invariant that session id function is collision-free across shops, and leading to: cross-tenant access?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `customAppSession`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session token header with unexpected casing/whitespace
- Exploit idea: getJwtSessionId/getOfflineId maps a session token header with unexpected casing/whitespace to another shop's id
- Invariant to test: session id function is collision-free across shops
- Expected Immunefi impact: Cross-tenant access (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: collision search test
