# Q2876: oauth/refresh-token — cookie forgery

## Question
Can an unprivileged attacker submit a token-exchange call with a session token for another shop to `refreshToken` in `oauth/refresh-token.ts` such that the OAuth signed cookie is forgeable/settable via a token-exchange call with a session token for another shop, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a token-exchange call with a session token for another shop
- Exploit idea: the OAuth signed cookie is forgeable/settable via a token-exchange call with a session token for another shop
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
