# Q4977: helpers/validate-session-token — claim-shape confusion

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `validateSessionToken` in `helpers/validate-session-token.ts` such that validateSessionToken mishandles array/duplicate claims in an expired JWT (exp in the past) or one with nbf in the future, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/validate-session-token.ts` -> `validateSessionToken`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: validateSessionToken mishandles array/duplicate claims in an expired JWT (exp in the past) or one with nbf in the future
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
