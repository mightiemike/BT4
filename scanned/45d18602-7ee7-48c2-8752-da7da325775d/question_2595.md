# Q2595: auth/auth-callback — cookie forgery

## Question
Can an unprivileged attacker submit a forged or attacker-set OAuth signed cookie to `handleCallbackError` in `auth/auth-callback.ts` such that the OAuth signed cookie is forgeable/settable via a forged or attacker-set OAuth signed cookie, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-app-express/src/auth/auth-callback.ts` -> `handleCallbackError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a forged or attacker-set OAuth signed cookie
- Exploit idea: the OAuth signed cookie is forgeable/settable via a forged or attacker-set OAuth signed cookie
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
