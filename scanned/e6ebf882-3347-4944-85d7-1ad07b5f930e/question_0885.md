# Q0885: auth/auth-callback — nonce replay

## Question
Can an unprivileged attacker submit a callback replaying a previously-used nonce to `handleCallbackError` in `auth/auth-callback.ts` such that handleCallbackError allows reuse of a consumed nonce for a callback replaying a previously-used nonce, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `handleCallbackError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback replaying a previously-used nonce
- Exploit idea: handleCallbackError allows reuse of a consumed nonce for a callback replaying a previously-used nonce
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
