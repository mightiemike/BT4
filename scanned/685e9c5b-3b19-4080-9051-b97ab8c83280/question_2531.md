# Q2531: oauth/create-session — cookie forgery

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `createSession` in `oauth/create-session.ts` such that the OAuth signed cookie is forgeable/settable via a callback whose shop param differs from the begin request, breaking the invariant that cookie integrity via app secret, and leading to: session fixation / csrf?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: the OAuth signed cookie is forgeable/settable via a callback whose shop param differs from the begin request
- Invariant to test: cookie integrity via app secret
- Expected Immunefi impact: Session fixation / CSRF (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forge cookie signature test
