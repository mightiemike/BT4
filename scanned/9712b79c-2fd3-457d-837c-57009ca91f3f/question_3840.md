# Q3840: oauth/oauth — callback hmac skip

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `throwIfCustomStoreApp` in `oauth/oauth.ts` such that validQuery/throwIfCustomStoreApp accepts a callback missing/invalid hmac for a client-credentials grant triggered without shop verification, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `throwIfCustomStoreApp`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: validQuery/throwIfCustomStoreApp accepts a callback missing/invalid hmac for a client-credentials grant triggered without shop verification
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
