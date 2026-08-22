# Q2361: oauth/token-exchange — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `tokenExchange` in `oauth/token-exchange.ts` such that tokenExchange does not require callback shop==begin shop for an online-vs-offline token-type confusion in the grant, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` -> `tokenExchange`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: tokenExchange does not require callback shop==begin shop for an online-vs-offline token-type confusion in the grant
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
