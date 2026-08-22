# Q1968: auth/auth-callback — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `authCallback` in `auth/auth-callback.ts` such that authCallback does not require callback shop==begin shop for a begin request with an attacker-chosen shop domain, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `authCallback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: authCallback does not require callback shop==begin shop for a begin request with an attacker-chosen shop domain
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
