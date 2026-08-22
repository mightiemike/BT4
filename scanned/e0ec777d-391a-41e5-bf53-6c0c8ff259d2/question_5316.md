# Q5316: session/session-utils — claim-shape confusion

## Question
Can an unprivileged attacker submit a session token header with unexpected casing/whitespace to `getOfflineId` in `session/session-utils.ts` such that getOfflineId mishandles array/duplicate claims in a session token header with unexpected casing/whitespace, breaking the invariant that single scalar claim enforced, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/session/session-utils.ts` -> `getOfflineId`
- Entrypoint: Authenticated admin request carrying a session-token (JWT)
- Attacker controls: a session token header with unexpected casing/whitespace
- Exploit idea: getOfflineId mishandles array/duplicate claims in a session token header with unexpected casing/whitespace
- Invariant to test: single scalar claim enforced
- Expected Immunefi impact: Auth bypass (In scope: authentication bypass / forged session for another shop. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: duplicate-claim token test
