# Q2585: helpers/get-session-token — expiry/nbf bypass

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `getSessionTokenHeader` in `helpers/get-session-token.ts` such that getSessionTokenHeader accepts an expired JWT (exp in the past) or one with nbf in the future despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: getSessionTokenHeader accepts an expired JWT (exp in the past) or one with nbf in the future despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
