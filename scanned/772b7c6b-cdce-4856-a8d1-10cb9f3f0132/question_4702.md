# Q4702: strategies/token-exchange — custom-app path bypass

## Question
Can an unprivileged attacker submit a callback whose signed cookie belongs to a different browser to `exchangeToken` in `strategies/token-exchange.ts` such that throwIfCustomStoreApp/exchangeToken fails to reject a callback whose signed cookie belongs to a different browser on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `exchangeToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose signed cookie belongs to a different browser
- Exploit idea: throwIfCustomStoreApp/exchangeToken fails to reject a callback whose signed cookie belongs to a different browser on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
