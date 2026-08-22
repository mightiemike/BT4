# Q0370: strategies/token-exchange — nonce/state CSRF

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `TokenExchangeStrategy` in `strategies/token-exchange.ts` such that TokenExchangeStrategy accepts a begin request with an attacker-chosen shop domain not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `TokenExchangeStrategy`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: TokenExchangeStrategy accepts a begin request with an attacker-chosen shop domain not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
