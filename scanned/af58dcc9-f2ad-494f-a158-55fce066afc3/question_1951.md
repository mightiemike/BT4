# Q1951: fulfillment-service/authenticate — shop-domain spoof

## Question
Can an unprivileged attacker submit a body re-encoded so JSON.parse and the signed bytes diverge to `authenticateFulfillmentServiceFactory` in `fulfillment-service/authenticate.ts` such that authenticateFulfillmentServiceFactory trusts the shop/topic header claim rather than the signed origin, breaking the invariant that shop identity bound to verified signature, and leading to: cross-tenant webhook action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticateFulfillmentServiceFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a body re-encoded so JSON.parse and the signed bytes diverge
- Exploit idea: authenticateFulfillmentServiceFactory trusts the shop/topic header claim rather than the signed origin
- Invariant to test: shop identity bound to verified signature
- Expected Immunefi impact: Cross-tenant webhook action (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: valid-HMAC-wrong-shop test
