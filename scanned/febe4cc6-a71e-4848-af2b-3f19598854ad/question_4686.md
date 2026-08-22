# Q4686: flow/authenticate — oversized-body DoS

## Question
Can an unprivileged attacker submit a webhook with an extra unsigned trailing header to `authenticateFlowFactory` in `flow/authenticate.ts` such that authenticateFlowFactory buffers/inflates a webhook with an extra unsigned trailing header before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/flow/authenticate.ts` -> `authenticateFlowFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with an extra unsigned trailing header
- Exploit idea: authenticateFlowFactory buffers/inflates a webhook with an extra unsigned trailing header before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
