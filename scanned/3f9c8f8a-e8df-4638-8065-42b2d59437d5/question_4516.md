# Q4516: fulfillment-service/authenticate — oversized-body DoS

## Question
Can an unprivileged attacker submit a Flow/fulfillment payload with attacker-chosen fields to `authenticate` in `fulfillment-service/authenticate.ts` such that authenticate buffers/inflates a Flow/fulfillment payload with attacker-chosen fields before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a Flow/fulfillment payload with attacker-chosen fields
- Exploit idea: authenticate buffers/inflates a Flow/fulfillment payload with attacker-chosen fields before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
