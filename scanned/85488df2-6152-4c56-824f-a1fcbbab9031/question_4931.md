# Q4931: strategies/merchant-custom-app — token-exchange cross-shop

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `authenticate` in `strategies/merchant-custom-app.ts` such that authenticate exchanges a callback whose shop param differs from the begin request (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `authenticate`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: authenticate exchanges a callback whose shop param differs from the begin request (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
