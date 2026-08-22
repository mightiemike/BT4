# Q4757: oauth/refresh-token — custom-app path bypass

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `refreshToken` in `oauth/refresh-token.ts` such that throwIfCustomStoreApp/refreshToken fails to reject an online-vs-offline token-type confusion in the grant on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: throwIfCustomStoreApp/refreshToken fails to reject an online-vs-offline token-type confusion in the grant on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
