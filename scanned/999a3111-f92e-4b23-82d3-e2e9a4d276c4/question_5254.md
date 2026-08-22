# Q5254: src/webhooks/process — type-detection confusion

## Question
Can an unprivileged attacker submit an api-version header that selects a different validation path to `process` in `src/webhooks/process.ts` such that detectWebhookType mis-selects a validation branch for an api-version header that selects a different validation path, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-express/src/webhooks/process.ts` -> `process`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an api-version header that selects a different validation path
- Exploit idea: detectWebhookType mis-selects a validation branch for an api-version header that selects a different validation path
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
