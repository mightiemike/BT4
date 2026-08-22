# Q4410: oauth/oauth — custom-app path bypass

## Question
Can an unprivileged attacker submit a callback for a custom/merchant app path that should be rejected to `callback` in `oauth/oauth.ts` such that throwIfCustomStoreApp/callback fails to reject a callback for a custom/merchant app path that should be rejected on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `callback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback for a custom/merchant app path that should be rejected
- Exploit idea: throwIfCustomStoreApp/callback fails to reject a callback for a custom/merchant app path that should be rejected on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
