# Q0873: helpers/validate-session-token — audience not checked

## Question
Can an unprivileged attacker submit a JWT whose aud does not equal the app apiKey to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken skips or weakly checks aud for a JWT whose aud does not equal the app apiKey, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT whose aud does not equal the app apiKey
- Exploit idea: validateSessionToken skips or weakly checks aud for a JWT whose aud does not equal the app apiKey
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
