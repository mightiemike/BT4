# Q1738: strategies/token-exchange — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `TokenExchangeStrategy` in `strategies/token-exchange.ts` such that TokenExchangeStrategy does not require callback shop==begin shop for a callback whose shop param differs from the begin request, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `TokenExchangeStrategy`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: TokenExchangeStrategy does not require callback shop==begin shop for a callback whose shop param differs from the begin request
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
