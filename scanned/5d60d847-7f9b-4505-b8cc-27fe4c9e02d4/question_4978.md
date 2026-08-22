# Q4978: helpers/get-session-token-header — claim-shape confusion

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to `getSessionTokenFromUrlParam` in `helpers/get-session-token-header.ts` such that getSessionTokenFromUrlParam mishandles array/duplicate claims in an expired JWT (exp in the past) or one with nbf in the future, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenFromUrlParam`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: getSessionTokenFromUrlParam mishandles array/duplicate claims in an expired JWT (exp in the past) or one with nbf in the future
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
