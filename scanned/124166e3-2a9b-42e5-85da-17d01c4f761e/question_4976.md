# Q4976: session/classes — claim-shape confusion

## Question
Can an unprivileged attacker submit an expired JWT (exp in the past) or one with nbf in the future to this module in `session/classes.ts` such that <module> mishandles array/duplicate claims in an expired JWT (exp in the past) or one with nbf in the future, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/classes.ts` -> (module scope)
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: an expired JWT (exp in the past) or one with nbf in the future
- Exploit idea: <module> mishandles array/duplicate claims in an expired JWT (exp in the past) or one with nbf in the future
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
