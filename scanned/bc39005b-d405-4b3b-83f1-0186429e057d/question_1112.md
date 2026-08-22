# Q1112: strategies/merchant-custom-app — nonce replay

## Question
Can an unprivileged attacker submit a code param controlled by the attacker to `handleClientError` in `strategies/merchant-custom-app.ts` such that handleClientError allows reuse of a consumed nonce for a code param controlled by the attacker, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a code param controlled by the attacker
- Exploit idea: handleClientError allows reuse of a consumed nonce for a code param controlled by the attacker
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
