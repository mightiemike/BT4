# Q5261: session/classes — claim-shape confusion

## Question
Can an unprivileged attacker submit a token whose sub encodes another user's id to this module in `session/classes.ts` such that <module> mishandles array/duplicate claims in a token whose sub encodes another user's id, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/classes.ts` -> (module scope)
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a token whose sub encodes another user's id
- Exploit idea: <module> mishandles array/duplicate claims in a token whose sub encodes another user's id
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
