# Q3563: strategies/merchant-custom-app — callback hmac skip

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `authenticate` in `strategies/merchant-custom-app.ts` such that validQuery/authenticate accepts a callback missing/invalid hmac for a begin request with an attacker-chosen shop domain, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `authenticate`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: validQuery/authenticate accepts a callback missing/invalid hmac for a begin request with an attacker-chosen shop domain
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
