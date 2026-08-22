# Q2818: oauth/client-credentials — cookie forgery

## Question
Can an unprivileged attacker submit a callback for a custom/merchant app path that should be rejected to `clientCredentials` in `oauth/client-credentials.ts` such that the OAuth signed cookie is forgeable/settable via a callback for a custom/merchant app path that should be rejected, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts` -> `clientCredentials`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback for a custom/merchant app path that should be rejected
- Exploit idea: the OAuth signed cookie is forgeable/settable via a callback for a custom/merchant app path that should be rejected
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
