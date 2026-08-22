# Q1046: helpers/get-session-token — audience not checked

## Question
Can an unprivileged attacker submit a JWT with a leeway-abusing exp just outside clock skew to `getSessionTokenHeader` in `helpers/get-session-token.ts` such that getSessionTokenHeader skips or weakly checks aud for a JWT with a leeway-abusing exp just outside clock skew, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a leeway-abusing exp just outside clock skew
- Exploit idea: getSessionTokenHeader skips or weakly checks aud for a JWT with a leeway-abusing exp just outside clock skew
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
