# Q3734: strategies/merchant-custom-app — callback hmac skip

## Question
Can an unprivileged attacker submit concurrent begin/callback requests racing the nonce cookie to `respondToOAuthRequests` in `strategies/merchant-custom-app.ts` such that validQuery/respondToOAuthRequests accepts a callback missing/invalid hmac for concurrent begin/callback requests racing the nonce cookie, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `respondToOAuthRequests`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: concurrent begin/callback requests racing the nonce cookie
- Exploit idea: validQuery/respondToOAuthRequests accepts a callback missing/invalid hmac for concurrent begin/callback requests racing the nonce cookie
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
