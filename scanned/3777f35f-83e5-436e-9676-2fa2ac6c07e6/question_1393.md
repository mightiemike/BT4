# Q1393: oauth/client-credentials — nonce replay

## Question
Can an unprivileged attacker submit a callback with host param pointing to attacker infra to `clientCredentials` in `oauth/client-credentials.ts` such that clientCredentials allows reuse of a consumed nonce for a callback with host param pointing to attacker infra, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts` -> `clientCredentials`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback with host param pointing to attacker infra
- Exploit idea: clientCredentials allows reuse of a consumed nonce for a callback with host param pointing to attacker infra
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
