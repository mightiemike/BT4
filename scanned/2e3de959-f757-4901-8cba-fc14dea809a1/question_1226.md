# Q1226: strategies/merchant-custom-app — nonce replay

## Question
Can an unprivileged attacker submit a callback for a custom/merchant app path that should be rejected to `respondToOAuthRequests` in `strategies/merchant-custom-app.ts` such that respondToOAuthRequests allows reuse of a consumed nonce for a callback for a custom/merchant app path that should be rejected, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `respondToOAuthRequests`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback for a custom/merchant app path that should be rejected
- Exploit idea: respondToOAuthRequests allows reuse of a consumed nonce for a callback for a custom/merchant app path that should be rejected
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
