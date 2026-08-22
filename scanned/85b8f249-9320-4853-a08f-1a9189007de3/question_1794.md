# Q1794: strategies/auth-code-flow — shop mismatch begin/callback

## Question
Can an unprivileged attacker submit a forged or attacker-set OAuth signed cookie to `getOfflineSessionId` in `strategies/auth-code-flow.ts` such that getOfflineSessionId does not require callback shop==begin shop for a forged or attacker-set OAuth signed cookie, breaking the invariant that shop constant across the flow, and leading to: token bound to victim shop?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts` -> `getOfflineSessionId`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a forged or attacker-set OAuth signed cookie
- Exploit idea: getOfflineSessionId does not require callback shop==begin shop for a forged or attacker-set OAuth signed cookie
- Invariant to test: shop constant across the flow
- Expected Immunefi impact: Token bound to victim shop (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop callback test
