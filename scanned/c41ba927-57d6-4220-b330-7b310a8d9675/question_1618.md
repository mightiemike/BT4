# Q1618: oauth/nonce — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `nonce` in `oauth/nonce.ts` such that nonce does not require callback shop==begin shop for an OAuth callback with a state/nonce not matching the signed cookie, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/nonce.ts` -> `nonce`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: nonce does not require callback shop==begin shop for an OAuth callback with a state/nonce not matching the signed cookie
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
