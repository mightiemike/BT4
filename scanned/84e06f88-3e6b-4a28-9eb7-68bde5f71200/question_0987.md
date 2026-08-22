# Q0987: helpers/validate-session-token — audience not checked

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken skips or weakly checks aud for an expired JWT (exp in the past) or one with nbf in the future, breaking the invariant that aud must equal apiKey, and leading to: token minted for another app/shop accepted?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: validateSessionToken skips or weakly checks aud for an expired JWT (exp in the past) or one with nbf in the future
- Invariant to test: aud must equal apiKey
- Expected Immunefi impact: Token minted for another app/shop accepted (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: aud-mismatch token test
