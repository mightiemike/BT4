# Q3215: oauth/create-session — callback hmac skip

## Question
Can an unprivileged attacker submit an OAuth callback with a state/nonce not matching the signed cookie to `createSession` in `oauth/create-session.ts` such that validQuery/createSession accepts a callback missing/invalid hmac for an OAuth callback with a state/nonce not matching the signed cookie, breaking the invariant that callback HMAC required, and leading to: forged callback accepted?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/oauth/create-session.ts` -> `createSession`
- Entrypoint: GET to the app's /auth begin or /auth/callback route
- Attacker controls: an OAuth callback with a state/nonce not matching the signed cookie
- Exploit idea: validQuery/createSession accepts a callback missing/invalid hmac for an OAuth callback with a state/nonce not matching the signed cookie
- Invariant to test: callback HMAC required
- Expected Immunefi impact: Forged callback accepted (In scope: OAuth CSRF, install hijack, or access-token theft. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: drop hmac param test
