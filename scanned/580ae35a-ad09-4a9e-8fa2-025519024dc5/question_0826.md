# Q0826: strategies/token-exchange — nonce replay

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `authenticate` in `strategies/token-exchange.ts` such that authenticate allows reuse of a consumed nonce for an OAuth callback with a state/nonce not matching the signed cookie, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts` -> `authenticate`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: authenticate allows reuse of a consumed nonce for an OAuth callback with a state/nonce not matching the signed cookie
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
