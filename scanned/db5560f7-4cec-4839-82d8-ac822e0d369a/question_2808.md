# Q2808: session/session-utils — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a JWT with duplicate or array-typed aud claims to `getOfflineId` in `session/session-utils.ts` such that getOfflineId accepts a JWT with duplicate or array-typed aud claims despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getOfflineId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with duplicate or array-typed aud claims
- Exploit idea: getOfflineId accepts a JWT with duplicate or array-typed aud claims despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
