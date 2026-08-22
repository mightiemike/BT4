# Q3447: strategies/auth-code-flow — callback hmac skip

## Question
Can an unprivileged attacker submit a callback missing the hmac param to `handleAuthBeginRequest` in `strategies/auth-code-flow.ts` such that validQuery/handleAuthBeginRequest accepts a callback missing/invalid hmac for a callback missing the hmac param, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `handleAuthBeginRequest`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback missing the hmac param
- Exploit idea: validQuery/handleAuthBeginRequest accepts a callback missing/invalid hmac for a callback missing the hmac param
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
