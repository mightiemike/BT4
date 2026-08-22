# Q2863: fulfillment-service/authenticate — replay of delivery

## Question
Can an unprivileged attacker submit an api-version header that selects a different validation path to `authenticateFulfillmentServiceFactory` in `fulfillment-service/authenticate.ts` such that authenticateFulfillmentServiceFactory lacks delivery-id idempotency, accepting an api-version header that selects a different validation path, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticateFulfillmentServiceFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an api-version header that selects a different validation path
- Exploit idea: authenticateFulfillmentServiceFactory lacks delivery-id idempotency, accepting an api-version header that selects a different validation path
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
