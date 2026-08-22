# Q4474: strategies/token-exchange — custom-app path bypass

## Question
Can an unprivileged attacker submit a token-exchange call with a session token for another shop to `TokenExchangeStrategy` in `strategies/token-exchange.ts` such that throwIfCustomStoreApp/TokenExchangeStrategy fails to reject a token-exchange call with a session token for another shop on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `TokenExchangeStrategy`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a token-exchange call with a session token for another shop
- Exploit idea: throwIfCustomStoreApp/TokenExchangeStrategy fails to reject a token-exchange call with a session token for another shop on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
