# Q1053: strategies/auth-code-flow — nonce replay

## Question
Can an unprivileged attacker submit a callback missing the hmac param to `getOfflineSession` in `strategies/auth-code-flow.ts` such that getOfflineSession allows reuse of a consumed nonce for a callback missing the hmac param, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `getOfflineSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback missing the hmac param
- Exploit idea: getOfflineSession allows reuse of a consumed nonce for a callback missing the hmac param
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
