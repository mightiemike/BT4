# Q5559: auth/auth-callback — token-exchange cross-shop

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `handleCallbackError` in `auth/auth-callback.ts` such that handleCallbackError exchanges an online-vs-offline token-type confusion in the grant (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `handleCallbackError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: handleCallbackError exchanges an online-vs-offline token-type confusion in the grant (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
