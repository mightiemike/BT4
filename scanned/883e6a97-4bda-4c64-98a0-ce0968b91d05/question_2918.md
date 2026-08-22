# Q2918: webhooks/authenticate — replay of delivery

## Question
Can an unprivileged attacker submit a Flow/fulfillment payload with attacker-chosen fields to `authenticate` in `webhooks/authenticate.ts` such that authenticate lacks delivery-id idempotency, accepting a Flow/fulfillment payload with attacker-chosen fields, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a Flow/fulfillment payload with attacker-chosen fields
- Exploit idea: authenticate lacks delivery-id idempotency, accepting a Flow/fulfillment payload with attacker-chosen fields
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
