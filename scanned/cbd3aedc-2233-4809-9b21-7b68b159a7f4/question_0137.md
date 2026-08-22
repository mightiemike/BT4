# Q0137: oauth/create-session — nonce/state CSRF

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `createSession` in `oauth/create-session.ts` such that createSession accepts a callback whose shop param differs from the begin request not bound to the signed begin cookie, breaking the invariant that callback state equals issued nonce, and leading to: oauth csrf / install hijack?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: createSession accepts a callback whose shop param differs from the begin request not bound to the signed begin cookie
- Invariant to test: callback state equals issued nonce
- Expected Immunefi impact: OAuth CSRF / install hijack (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: begin then callback with foreign state, expect reject
