# Q1608: flow/authenticate — shop-domain spoof

## Question
Can an unprivileged attacker submit a forged X-Shopify-Hmac-Sha256 header with a truncated digest to `authenticateFlowFactory` in `flow/authenticate.ts` such that authenticateFlowFactory trusts the shop/topic header claim rather than the signed origin, breaking the invariant that shop identity bound to verified signature, and leading to: cross-tenant webhook action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/flow/authenticate.ts` -> `authenticateFlowFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Exploit idea: authenticateFlowFactory trusts the shop/topic header claim rather than the signed origin
- Invariant to test: shop identity bound to verified signature
- Expected Immunefi impact: Cross-tenant webhook action (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: valid-HMAC-wrong-shop test
