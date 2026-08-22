# Q5496: oauth/token-exchange — token-exchange cross-shop

## Question
Can an unprivileged attacker submit a callback whose signed cookie belongs to a different browser to `tokenExchange` in `oauth/token-exchange.ts` such that tokenExchange exchanges a callback whose signed cookie belongs to a different browser (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts` -> `tokenExchange`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose signed cookie belongs to a different browser
- Exploit idea: tokenExchange exchanges a callback whose signed cookie belongs to a different browser (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
