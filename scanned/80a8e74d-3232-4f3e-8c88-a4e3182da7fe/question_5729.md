# Q5729: oauth/nonce — race on nonce cookie

## Question
Can an unprivileged attacker submit a callback missing the hmac param to `nonce` in `oauth/nonce.ts` such that concurrent a callback missing the hmac param races createSession/callback state, breaking the invariant that atomic single-use nonce, and leading to: csrf via race?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/nonce.ts` -> `nonce`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback missing the hmac param
- Exploit idea: concurrent a callback missing the hmac param races createSession/callback state
- Invariant to test: atomic single-use nonce
- Expected Immunefi impact: CSRF via race (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: parallel callback race test
