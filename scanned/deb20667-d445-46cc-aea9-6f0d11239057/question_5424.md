# Q5424: fulfillment-service/validate — type-detection confusion

## Question
Can an unprivileged attacker submit a chunked/streamed body read twice to `validate` in `fulfillment-service/validate.ts` such that detectWebhookType mis-selects a validation branch for a chunked/streamed body read twice, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` -> `validate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a chunked/streamed body read twice
- Exploit idea: detectWebhookType mis-selects a validation branch for a chunked/streamed body read twice
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
