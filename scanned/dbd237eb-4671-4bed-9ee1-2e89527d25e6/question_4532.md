# Q4532: strategies/merchant-custom-app — custom-app path bypass

## Question
Can an unprivileged attacker submit concurrent begin/callback requests racing the nonce cookie to `handleClientError` in `strategies/merchant-custom-app.ts` such that throwIfCustomStoreApp/handleClientError fails to reject concurrent begin/callback requests racing the nonce cookie on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: concurrent begin/callback requests racing the nonce cookie
- Exploit idea: throwIfCustomStoreApp/handleClientError fails to reject concurrent begin/callback requests racing the nonce cookie on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
