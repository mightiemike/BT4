# Q3156: oauth/oauth — cookie forgery

## Question
Can an unprivileged attacker submit an online-vs-offline token-type confusion in the grant to `throwIfCustomStoreApp` in `oauth/oauth.ts` such that the OAuth signed cookie is forgeable/settable via an online-vs-offline token-type confusion in the grant, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `throwIfCustomStoreApp`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an online-vs-offline token-type confusion in the grant
- Exploit idea: the OAuth signed cookie is forgeable/settable via an online-vs-offline token-type confusion in the grant
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
