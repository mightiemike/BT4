# Q3505: strategies/token-exchange — callback hmac skip

## Question
Can an unprivileged attacker submit a code param controlled by the attacker to `respondToOAuthRequests` in `strategies/token-exchange.ts` such that validQuery/respondToOAuthRequests accepts a callback missing/invalid hmac for a code param controlled by the attacker, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `respondToOAuthRequests`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a code param controlled by the attacker
- Exploit idea: validQuery/respondToOAuthRequests accepts a callback missing/invalid hmac for a code param controlled by the attacker
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
