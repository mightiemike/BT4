# Q0428: strategies/merchant-custom-app — nonce/state CSRF

## Question
Can an unprivileged attacker submit a callback for a custom/merchant app path that should be rejected to `handleClientError` in `strategies/merchant-custom-app.ts` such that handleClientError accepts a callback for a custom/merchant app path that should be rejected not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback for a custom/merchant app path that should be rejected
- Exploit idea: handleClientError accepts a callback for a custom/merchant app path that should be rejected not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
