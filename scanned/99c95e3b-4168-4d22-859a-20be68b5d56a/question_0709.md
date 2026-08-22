# Q0709: oauth/client-credentials — nonce/state CSRF

## Question
Can an unprivileged attacker submit a callback whose signed cookie belongs to a different browser to `clientCredentials` in `oauth/client-credentials.ts` such that clientCredentials accepts a callback whose signed cookie belongs to a different browser not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts` -> `clientCredentials`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose signed cookie belongs to a different browser
- Exploit idea: clientCredentials accepts a callback whose signed cookie belongs to a different browser not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
