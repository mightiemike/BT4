# Q5374: session/session — claim-shape confusion

## Question
Can an unprivileged attacker submit a JWT with an oversized payload triggering heavy verify work to `equals` in `session/session.ts` such that equals mishandles array/duplicate claims in a JWT with an oversized payload triggering heavy verify work, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session.ts` -> `equals`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with an oversized payload triggering heavy verify work
- Exploit idea: equals mishandles array/duplicate claims in a JWT with an oversized payload triggering heavy verify work
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
