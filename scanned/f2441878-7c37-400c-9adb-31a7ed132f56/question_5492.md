# Q5492: helpers/get-session-token — claim-shape confusion

## Question
Can an unprivileged attacker submit a JWT with a kid header steering key selection to `getSessionTokenHeader` in `helpers/get-session-token.ts` such that getSessionTokenHeader mishandles array/duplicate claims in a JWT with a kid header steering key selection, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-express/src/helpers/get-session-token.ts` -> `getSessionTokenHeader`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a kid header steering key selection
- Exploit idea: getSessionTokenHeader mishandles array/duplicate claims in a JWT with a kid header steering key selection
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
