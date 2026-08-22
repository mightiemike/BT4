# Q5479: lib/webhooks/process — type-detection confusion

## Question
Can an unprivileged attacker submit a webhook with an extra unsigned trailing header to `process` in `lib/webhooks/process.ts` such that detectWebhookType mis-selects a validation branch for a webhook with an extra unsigned trailing header, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `process`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with an extra unsigned trailing header
- Exploit idea: detectWebhookType mis-selects a validation branch for a webhook with an extra unsigned trailing header
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
