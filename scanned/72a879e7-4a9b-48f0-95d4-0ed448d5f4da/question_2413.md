# Q2413: helpers/get-session-token-header — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a JWT signed with alg=none or an unexpected alg header to `getSessionTokenHeader` in `helpers/get-session-token-header.ts` such that getSessionTokenHeader accepts a JWT signed with alg=none or an unexpected alg header despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT signed with alg=none or an unexpected alg header
- Exploit idea: getSessionTokenHeader accepts a JWT signed with alg=none or an unexpected alg header despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
