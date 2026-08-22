# Q0541: strategies/token-exchange — nonce/state CSRF

## Question
Can an unprivileged attacker submit concurrent begin/callback requests racing the nonce cookie to `handleClientError` in `strategies/token-exchange.ts` such that handleClientError accepts concurrent begin/callback requests racing the nonce cookie not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: concurrent begin/callback requests racing the nonce cookie
- Exploit idea: handleClientError accepts concurrent begin/callback requests racing the nonce cookie not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
