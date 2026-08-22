# Q1452: strategies/auth-code-flow — nonce replay

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `handleClientError` in `strategies/auth-code-flow.ts` such that handleClientError allows reuse of a consumed nonce for a client-credentials grant triggered without shop verification, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: handleClientError allows reuse of a consumed nonce for a client-credentials grant triggered without shop verification
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
