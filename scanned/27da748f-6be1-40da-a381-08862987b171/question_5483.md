# Q5483: webhooks/authenticate — type-detection confusion

## Question
Can an unprivileged attacker submit a webhook with an extra unsigned trailing header to `authenticateWebhookFactory` in `webhooks/authenticate.ts` such that detectWebhookType mis-selects a validation branch for a webhook with an extra unsigned trailing header, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticateWebhookFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with an extra unsigned trailing header
- Exploit idea: detectWebhookType mis-selects a validation branch for a webhook with an extra unsigned trailing header
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
