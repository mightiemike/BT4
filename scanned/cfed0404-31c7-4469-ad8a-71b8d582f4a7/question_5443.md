# Q5443: strategies/token-exchange — token-exchange cross-shop

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `handleAfterAuthHook` in `strategies/token-exchange.ts` such that handleAfterAuthHook exchanges a client-credentials grant triggered without shop verification (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `handleAfterAuthHook`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: handleAfterAuthHook exchanges a client-credentials grant triggered without shop verification (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
