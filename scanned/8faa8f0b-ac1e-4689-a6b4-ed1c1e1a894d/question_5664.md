# Q5664: oauth/client-credentials — race on nonce cookie

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `clientCredentials` in `oauth/client-credentials.ts` such that concurrent a callback whose shop param differs from the begin request races createSession/callback state, breaking the invariant that atomic single-use nonce, and leading to: csrf via race?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts` -> `clientCredentials`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: concurrent a callback whose shop param differs from the begin request races createSession/callback state
- Invariant to test: atomic single-use nonce
- Expected Immunefi impact: CSRF via race (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: parallel callback race test
