# Q2698: helpers/get-session-token-header — expiry/nbf bypass

## Question
Can an unprivileged attacker submit a session id derived from an attacker-chosen dest claim to `getSessionTokenFromUrlParam` in `helpers/get-session-token-header.ts` such that getSessionTokenFromUrlParam accepts a session id derived from an attacker-chosen dest claim despite exp/nbf constraints, breaking the invariant that expired or not-yet-valid tokens rejected, and leading to: session replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenFromUrlParam`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session id derived from an attacker-chosen dest claim
- Exploit idea: getSessionTokenFromUrlParam accepts a session id derived from an attacker-chosen dest claim despite exp/nbf constraints
- Invariant to test: expired or not-yet-valid tokens rejected
- Expected Immunefi impact: Session replay (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: boundary exp/nbf tests
