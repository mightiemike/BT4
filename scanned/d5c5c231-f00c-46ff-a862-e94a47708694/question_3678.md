# Q3678: auth/auth-callback — callback hmac skip

## Question
Can an unprivileged attacker submit a token-exchange call with a session token for another shop to `authCallback` in `auth/auth-callback.ts` such that validQuery/authCallback accepts a callback missing/invalid hmac for a token-exchange call with a session token for another shop, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `authCallback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a token-exchange call with a session token for another shop
- Exploit idea: validQuery/authCallback accepts a callback missing/invalid hmac for a token-exchange call with a session token for another shop
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
