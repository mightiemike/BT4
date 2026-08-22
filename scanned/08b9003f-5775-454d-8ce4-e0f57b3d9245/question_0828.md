# Q0828: auth/auth-callback — nonce replay

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `authCallback` in `auth/auth-callback.ts` such that authCallback allows reuse of a consumed nonce for an OAuth callback with a state/nonce not matching the signed cookie, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `authCallback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: authCallback allows reuse of a consumed nonce for an OAuth callback with a state/nonce not matching the signed cookie
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
