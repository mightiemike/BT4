# Q2420: oauth/refresh-token — cookie forgery

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `refreshToken` in `oauth/refresh-token.ts` such that the OAuth signed cookie is forgeable/settable via an OAuth callback with a state/nonce not matching the signed cookie, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/refresh-token.ts` -> `refreshToken`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: the OAuth signed cookie is forgeable/settable via an OAuth callback with a state/nonce not matching the signed cookie
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
