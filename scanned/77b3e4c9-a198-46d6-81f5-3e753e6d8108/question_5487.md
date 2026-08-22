# Q5487: session/session-utils — claim-shape confusion

## Question
Can an unprivileged attacker submit a JWT with a kid header steering key selection to `getJwtSessionId` in `session/session-utils.ts` such that getJwtSessionId mishandles array/duplicate claims in a JWT with a kid header steering key selection, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getJwtSessionId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a JWT with a kid header steering key selection
- Exploit idea: getJwtSessionId mishandles array/duplicate claims in a JWT with a kid header steering key selection
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
