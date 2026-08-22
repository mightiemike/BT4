# Q2309: strategies/merchant-custom-app — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit a callback whose signed cookie belongs to a different browser to `MerchantCustomAuth` in `strategies/merchant-custom-app.ts` such that MerchantCustomAuth does not require callback shop==begin shop for a callback whose signed cookie belongs to a different browser, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `MerchantCustomAuth`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose signed cookie belongs to a different browser
- Exploit idea: MerchantCustomAuth does not require callback shop==begin shop for a callback whose signed cookie belongs to a different browser
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
