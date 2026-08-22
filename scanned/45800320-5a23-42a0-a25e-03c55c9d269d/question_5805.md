# Q5805: auth/auth-callback — race on nonce cookie

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `authCallback` in `auth/auth-callback.ts` such that concurrent a begin request with an attacker-chosen shop domain races createSession/callback state, breaking the invariant that atomic single-use nonce, and leading to: csrf via race?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `authCallback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: concurrent a begin request with an attacker-chosen shop domain races createSession/callback state
- Invariant to test: atomic single-use nonce
- Expected Immunefi impact: CSRF via race (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: parallel callback race test
