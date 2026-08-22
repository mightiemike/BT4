# Q5210: oauth/create-session — token-exchange cross-shop

## Question
Can an unprivileged attacker submit a callback for a custom/merchant app path that should be rejected to `createSession` in `oauth/create-session.ts` such that createSession exchanges a callback for a custom/merchant app path that should be rejected (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: a callback for a custom/merchant app path that should be rejected
- Exploit idea: createSession exchanges a callback for a custom/merchant app path that should be rejected (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
