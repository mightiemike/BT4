# Q0995: oauth/refresh-token — nonce replay

## Question
Can an unprivileged attacker submit a forged or attacker-set OAuth signed cookie to `refreshToken` in `oauth/refresh-token.ts` such that refreshToken allows reuse of a consumed nonce for a forged or attacker-set OAuth signed cookie, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a forged or attacker-set OAuth signed cookie
- Exploit idea: refreshToken allows reuse of a consumed nonce for a forged or attacker-set OAuth signed cookie
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
