# Q1331: helpers/get-session-token — audience not checked

## Question
Can an unprivileged attacker submit a session token header with unexpected casing/whitespace to `getSessionToken` in `helpers/get-session-token.ts` such that getSessionToken skips or weakly checks aud for a session token header with unexpected casing/whitespace, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session token header with unexpected casing/whitespace
- Exploit idea: getSessionToken skips or weakly checks aud for a session token header with unexpected casing/whitespace
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
