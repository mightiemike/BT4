# Q2766: auth/auth-callback — cookie forgery

## Question
Can an unprivileged attacker submit a begin request with an attacker-chosen shop domain to `authCallback` in `auth/auth-callback.ts` such that the OAuth signed cookie is forgeable/settable via a begin request with an attacker-chosen shop domain, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `authCallback`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a begin request with an attacker-chosen shop domain
- Exploit idea: the OAuth signed cookie is forgeable/settable via a begin request with an attacker-chosen shop domain
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
