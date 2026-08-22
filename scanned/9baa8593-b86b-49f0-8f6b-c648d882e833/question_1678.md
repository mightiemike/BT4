# Q1678: oauth/client-credentials — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit a callback replaying a previously-used nonce to `clientCredentials` in `oauth/client-credentials.ts` such that clientCredentials does not require callback shop==begin shop for a callback replaying a previously-used nonce, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts` -> `clientCredentials`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback replaying a previously-used nonce
- Exploit idea: clientCredentials does not require callback shop==begin shop for a callback replaying a previously-used nonce
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
