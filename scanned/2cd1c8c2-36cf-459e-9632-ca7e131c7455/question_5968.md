# Q5968: oauth/create-session — race on nonce cookie

## Question
Can an unprivileged attacker submit a client-credentials grant triggered without shop verification to `createSession` in `oauth/create-session.ts` such that concurrent a client-credentials grant triggered without shop verification races createSession/callback state, breaking the invariant that atomic single-use nonce, and leading to: csrf via race?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a client-credentials grant triggered without shop verification
- Exploit idea: concurrent a client-credentials grant triggered without shop verification races createSession/callback state
- Invariant to test: atomic single-use nonce
- Expected Immunefi impact: CSRF via race (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: parallel callback race test
