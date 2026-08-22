# Q5699: oauth/refresh-token — race on nonce cookie

## Question
Can an unprivileged attacker submit a forged or attacker-set OAuth signed cookie to `refreshToken` in `oauth/refresh-token.ts` such that concurrent a forged or attacker-set OAuth signed cookie races createSession/callback state, breaking the invariant that atomic single-use nonce, and leading to: csrf via race?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a forged or attacker-set OAuth signed cookie
- Exploit idea: concurrent a forged or attacker-set OAuth signed cookie races createSession/callback state
- Invariant to test: atomic single-use nonce
- Expected Immunefi impact: CSRF via race (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: parallel callback race test
