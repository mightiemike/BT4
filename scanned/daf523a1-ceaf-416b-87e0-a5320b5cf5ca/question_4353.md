# Q4353: oauth/oauth — custom-app path bypass

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `begin` in `oauth/oauth.ts` such that throwIfCustomStoreApp/begin fails to reject a begin request with an attacker-chosen shop domain on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `begin`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: throwIfCustomStoreApp/begin fails to reject a begin request with an attacker-chosen shop domain on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
