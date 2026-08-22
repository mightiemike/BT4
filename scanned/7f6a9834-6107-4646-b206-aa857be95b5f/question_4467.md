# Q4467: oauth/oauth — custom-app path bypass

## Question
Can an unprivileged attacker submit a token-exchange call with a session token for another shop to `validQuery` in `oauth/oauth.ts` such that throwIfCustomStoreApp/validQuery fails to reject a token-exchange call with a session token for another shop on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `validQuery`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a token-exchange call with a session token for another shop
- Exploit idea: throwIfCustomStoreApp/validQuery fails to reject a token-exchange call with a session token for another shop on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
