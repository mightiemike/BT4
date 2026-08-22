# Q1505: oauth/create-session — nonce replay

## Question
Can an unprivileged attacker submit a callback whose signed cookie belongs to a different browser to `createSession` in `oauth/create-session.ts` such that createSession allows reuse of a consumed nonce for a callback whose signed cookie belongs to a different browser, breaking the invariant that nonce single-use, and leading to: auth csrf replay?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose signed cookie belongs to a different browser
- Exploit idea: createSession allows reuse of a consumed nonce for a callback whose signed cookie belongs to a different browser
- Invariant to test: nonce single-use
- Expected Immunefi impact: Auth CSRF replay (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay callback twice
