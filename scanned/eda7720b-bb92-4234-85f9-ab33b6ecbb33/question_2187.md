# Q2187: oauth/oauth — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit a callback with host param pointing to attacker infra to `validQuery` in `oauth/oauth.ts` such that validQuery does not require callback shop==begin shop for a callback with host param pointing to attacker infra, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `validQuery`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback with host param pointing to attacker infra
- Exploit idea: validQuery does not require callback shop==begin shop for a callback with host param pointing to attacker infra
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
