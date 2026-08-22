# Q0599: strategies/merchant-custom-app — nonce/state CSRF

## Question
Can an unprivileged attacker submit a callback with host param pointing to attacker infra to `authenticate` in `strategies/merchant-custom-app.ts` such that authenticate accepts a callback with host param pointing to attacker infra not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `authenticate`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback with host param pointing to attacker infra
- Exploit idea: authenticate accepts a callback with host param pointing to attacker infra not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
