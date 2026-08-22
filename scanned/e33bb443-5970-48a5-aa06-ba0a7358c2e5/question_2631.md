# Q2631: fulfillment-service/validate — replay of delivery

## Question
Can an unprivileged attacker submit a duplicated webhook delivery id (replay) to `validateFactory` in `fulfillment-service/validate.ts` such that validateFactory lacks delivery-id idempotency, accepting a duplicated webhook delivery id (replay), breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` -> `validateFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a duplicated webhook delivery id (replay)
- Exploit idea: validateFactory lacks delivery-id idempotency, accepting a duplicated webhook delivery id (replay)
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
