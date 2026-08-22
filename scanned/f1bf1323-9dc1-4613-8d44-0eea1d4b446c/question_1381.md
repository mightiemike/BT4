# Q1381: fulfillment-service/authenticate — missing-header fallthrough

## Question
Can an unprivileged attacker submit a webhook with valid HMAC but spoofed shop domain claim to `authenticateFulfillmentServiceFactory` in `fulfillment-service/authenticate.ts` such that authenticateFulfillmentServiceFactory proceeds when a required webhook header is absent for a webhook with valid HMAC but spoofed shop domain claim, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticateFulfillmentServiceFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with valid HMAC but spoofed shop domain claim
- Exploit idea: authenticateFulfillmentServiceFactory proceeds when a required webhook header is absent for a webhook with valid HMAC but spoofed shop domain claim
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
