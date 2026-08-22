# Q4759: strategies/token-exchange — custom-app path bypass

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `handleAfterAuthHook` in `strategies/token-exchange.ts` such that throwIfCustomStoreApp/handleAfterAuthHook fails to reject an online-vs-offline token-type confusion in the grant on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `handleAfterAuthHook`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: throwIfCustomStoreApp/handleAfterAuthHook fails to reject an online-vs-offline token-type confusion in the grant on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
