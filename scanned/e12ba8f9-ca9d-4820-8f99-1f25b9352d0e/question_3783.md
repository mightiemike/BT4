# Q3783: oauth/oauth — callback hmac skip

## Question
Can an unprivileged attacker submit a callback with host param pointing to attacker infra to `validQuery` in `oauth/oauth.ts` such that validQuery/validQuery accepts a callback missing/invalid hmac for a callback with host param pointing to attacker infra, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `validQuery`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback with host param pointing to attacker infra
- Exploit idea: validQuery/validQuery accepts a callback missing/invalid hmac for a callback with host param pointing to attacker infra
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
