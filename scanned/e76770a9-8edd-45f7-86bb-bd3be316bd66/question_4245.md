# Q4245: strategies/auth-code-flow — custom-app path bypass

## Question
Can an unprivileged attacker submit a callback missing the hmac param to `hasValidOfflineId` in `strategies/auth-code-flow.ts` such that throwIfCustomStoreApp/hasValidOfflineId fails to reject a callback missing the hmac param on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `hasValidOfflineId`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback missing the hmac param
- Exploit idea: throwIfCustomStoreApp/hasValidOfflineId fails to reject a callback missing the hmac param on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
