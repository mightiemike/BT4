# Q3960: strategies/auth-code-flow — callback hmac skip

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `handleClientError` in `strategies/auth-code-flow.ts` such that validQuery/handleClientError accepts a callback missing/invalid hmac for an online-vs-offline token-type confusion in the grant, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: validQuery/handleClientError accepts a callback missing/invalid hmac for an online-vs-offline token-type confusion in the grant
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
