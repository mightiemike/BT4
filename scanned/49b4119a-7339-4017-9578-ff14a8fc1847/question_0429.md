# Q0429: auth/auth-callback — nonce/state CSRF

## Question
Can an unprivileged attacker submit a callback for a custom/merchant app path that should be rejected to `handleCallbackError` in `auth/auth-callback.ts` such that handleCallbackError accepts a callback for a custom/merchant app path that should be rejected not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `handleCallbackError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback for a custom/merchant app path that should be rejected
- Exploit idea: handleCallbackError accepts a callback for a custom/merchant app path that should be rejected not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
