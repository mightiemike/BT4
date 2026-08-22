# Q4133: strategies/merchant-custom-app — custom-app path bypass

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `MerchantCustomAuth` in `strategies/merchant-custom-app.ts` such that throwIfCustomStoreApp/MerchantCustomAuth fails to reject a callback whose shop param differs from the begin request on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `MerchantCustomAuth`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: throwIfCustomStoreApp/MerchantCustomAuth fails to reject a callback whose shop param differs from the begin request on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
