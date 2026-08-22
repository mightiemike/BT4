# Q0924: flow/authenticate — missing-header fallthrough

## Question
Can an unprivileged attacker submit a webhook topic/domain header pointing at another shop to `authenticateFlowFactory` in `flow/authenticate.ts` such that authenticateFlowFactory proceeds when a required webhook header is absent for a webhook topic/domain header pointing at another shop, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/flow/authenticate.ts` -> `authenticateFlowFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook topic/domain header pointing at another shop
- Exploit idea: authenticateFlowFactory proceeds when a required webhook header is absent for a webhook topic/domain header pointing at another shop
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
