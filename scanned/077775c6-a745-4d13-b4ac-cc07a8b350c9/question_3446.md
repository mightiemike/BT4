# Q3446: oauth/refresh-token — callback hmac skip

## Question
Can an unprivileged attacker submit a callback missing the hmac param to `refreshToken` in `oauth/refresh-token.ts` such that validQuery/refreshToken accepts a callback missing/invalid hmac for a callback missing the hmac param, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback missing the hmac param
- Exploit idea: validQuery/refreshToken accepts a callback missing/invalid hmac for a callback missing the hmac param
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
