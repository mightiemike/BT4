# Q2805: flow/authenticate — replay of delivery

## Question
Can an unprivileged attacker submit an unexpected content-type on the webhook POST to `authenticate` in `flow/authenticate.ts` such that authenticate lacks delivery-id idempotency, accepting an unexpected content-type on the webhook POST, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/flow/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an unexpected content-type on the webhook POST
- Exploit idea: authenticate lacks delivery-id idempotency, accepting an unexpected content-type on the webhook POST
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
