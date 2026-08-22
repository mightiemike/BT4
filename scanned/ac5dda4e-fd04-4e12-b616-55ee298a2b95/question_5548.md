# Q5548: helpers/get-session-token-header — claim-shape confusion

## Question
Can an unprivileged attacker submit a token whose exp is a string instead of a number to `getSessionTokenFromUrlParam` in `helpers/get-session-token-header.ts` such that getSessionTokenFromUrlParam mishandles array/duplicate claims in a token whose exp is a string instead of a number, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-session-token-header.ts` -> `getSessionTokenFromUrlParam`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose exp is a string instead of a number
- Exploit idea: getSessionTokenFromUrlParam mishandles array/duplicate claims in a token whose exp is a string instead of a number
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
