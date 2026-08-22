# Q0026: oauth/refresh-token — nonce/state CSRF

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `refreshToken` in `oauth/refresh-token.ts` such that refreshToken accepts an OAuth callback with a state/nonce not matching the signed cookie not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: refreshToken accepts an OAuth callback with a state/nonce not matching the signed cookie not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
