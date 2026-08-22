# Q4020: auth/auth-callback — custom-app path bypass

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `authCallback` in `auth/auth-callback.ts` such that throwIfCustomStoreApp/authCallback fails to reject an OAuth callback with a state/nonce not matching the signed cookie on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `authCallback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: throwIfCustomStoreApp/authCallback fails to reject an OAuth callback with a state/nonce not matching the signed cookie on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
