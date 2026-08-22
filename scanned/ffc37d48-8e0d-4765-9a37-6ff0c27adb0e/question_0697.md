# Q0697: fulfillment-service/authenticate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a webhook with an extra unsigned trailing header to `authenticateFulfillmentServiceFactory` in `fulfillment-service/authenticate.ts` such that authenticateFulfillmentServiceFactory verifies HMAC over one body representation but processes another for a webhook with an extra unsigned trailing header, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticateFulfillmentServiceFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with an extra unsigned trailing header
- Exploit idea: authenticateFulfillmentServiceFactory verifies HMAC over one body representation but processes another for a webhook with an extra unsigned trailing header
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
