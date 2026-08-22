# Q3789: strategies/auth-code-flow — callback hmac skip

## Question
Can an unprivileged attacker submit a callback with host param pointing to attacker infra to `AuthCodeFlowStrategy` in `strategies/auth-code-flow.ts` such that validQuery/AuthCodeFlowStrategy accepts a callback missing/invalid hmac for a callback with host param pointing to attacker infra, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `AuthCodeFlowStrategy`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback with host param pointing to attacker infra
- Exploit idea: validQuery/AuthCodeFlowStrategy accepts a callback missing/invalid hmac for a callback with host param pointing to attacker infra
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
