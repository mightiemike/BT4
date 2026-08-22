# Q1446: oauth/oauth — nonce replay

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `callback` in `oauth/oauth.ts` such that callback allows reuse of a consumed nonce for a client-credentials grant triggered without shop verification, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `callback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: callback allows reuse of a consumed nonce for a client-credentials grant triggered without shop verification
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
