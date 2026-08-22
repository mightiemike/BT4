# Q1169: strategies/merchant-custom-app — nonce replay

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `MerchantCustomAuth` in `strategies/merchant-custom-app.ts` such that MerchantCustomAuth allows reuse of a consumed nonce for a begin request with an attacker-chosen shop domain, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `MerchantCustomAuth`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: MerchantCustomAuth allows reuse of a consumed nonce for a begin request with an attacker-chosen shop domain
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
