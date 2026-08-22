# Q4972: fulfillment-service/authenticate — type-detection confusion

## Question
Can an unprivileged attacker submit a request missing one required webhook header to `authenticate` in `fulfillment-service/authenticate.ts` such that detectWebhookType mis-selects a validation branch for a request missing one required webhook header, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a request missing one required webhook header
- Exploit idea: detectWebhookType mis-selects a validation branch for a request missing one required webhook header
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
