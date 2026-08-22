# Q5271: strategies/auth-code-flow — token-exchange cross-shop

## Question
Can an unprivileged attacker submit a token-exchange call with a session token for another shop to `ensureInstalledOnShop` in `strategies/auth-code-flow.ts` such that ensureInstalledOnShop exchanges a token-exchange call with a session token for another shop (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `ensureInstalledOnShop`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a token-exchange call with a session token for another shop
- Exploit idea: ensureInstalledOnShop exchanges a token-exchange call with a session token for another shop (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
