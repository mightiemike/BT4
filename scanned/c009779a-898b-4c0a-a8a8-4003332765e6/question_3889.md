# Q3889: fulfillment-service/authenticate — handler dispatch bypass

## Question
Can an unprivileged attacker submit a webhook with an extra unsigned trailing header to `authenticateFulfillmentServiceFactory` in `fulfillment-service/authenticate.ts` such that process/callWebhookHandlers routes a webhook with an extra unsigned trailing header to a handler without full validation, breaking the invariant that dispatch happens only post-verification, and leading to: forged handler invocation?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticateFulfillmentServiceFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with an extra unsigned trailing header
- Exploit idea: process/callWebhookHandlers routes a webhook with an extra unsigned trailing header to a handler without full validation
- Invariant to test: dispatch happens only post-verification
- Expected Immunefi impact: Forged handler invocation (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert handler count 0 for bad HMAC
