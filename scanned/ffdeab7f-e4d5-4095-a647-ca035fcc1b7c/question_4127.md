# Q4127: oauth/create-session — custom-app path bypass

## Question
Can an unprivileged attacker submit a callback whose shop param differs from the begin request to `createSession` in `oauth/create-session.ts` such that throwIfCustomStoreApp/createSession fails to reject a callback whose shop param differs from the begin request on a custom-app config, breaking the invariant that custom-app flows gated as intended, and leading to: unintended token grant?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback whose shop param differs from the begin request
- Exploit idea: throwIfCustomStoreApp/createSession fails to reject a callback whose shop param differs from the begin request on a custom-app config
- Invariant to test: custom-app flows gated as intended
- Expected Immunefi impact: Unintended token grant (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: custom-app path test
