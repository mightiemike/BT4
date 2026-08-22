# Q1566: strategies/auth-code-flow — nonce replay

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `handleAuthBeginRequest` in `strategies/auth-code-flow.ts` such that handleAuthBeginRequest allows reuse of a consumed nonce for an online-vs-offline token-type confusion in the grant, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `handleAuthBeginRequest`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: handleAuthBeginRequest allows reuse of a consumed nonce for an online-vs-offline token-type confusion in the grant
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
