# Q4809: oauth/oauth — token-exchange cross-shop

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `begin` in `oauth/oauth.ts` such that begin exchanges an OAuth callback with a state/nonce not matching the signed cookie (token for shop B) into a session for shop A, breaking the invariant that exchanged token bound to token's shop, and leading to: cross-tenant token minting?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts` -> `begin`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: begin exchanges an OAuth callback with a state/nonce not matching the signed cookie (token for shop B) into a session for shop A
- Invariant to test: exchanged token bound to token's shop
- Expected Immunefi impact: Cross-tenant token minting (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: cross-shop exchange test
