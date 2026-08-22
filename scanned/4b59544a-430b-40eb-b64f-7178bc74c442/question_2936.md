# Q2936: strategies/merchant-custom-app — cookie forgery

## Question
Can an unprivileged attacker submit concurrent begin/callback requests racing the nonce cookie to `handleClientError` in `strategies/merchant-custom-app.ts` such that the OAuth signed cookie is forgeable/settable via concurrent begin/callback requests racing the nonce cookie, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/merchant-custom-app.ts` -> `handleClientError`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: concurrent begin/callback requests racing the nonce cookie
- Exploit idea: the OAuth signed cookie is forgeable/settable via concurrent begin/callback requests racing the nonce cookie
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
