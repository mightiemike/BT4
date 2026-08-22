# Q5081: flow/validate — type-detection confusion

## Question
Can an unprivileged attacker submit an oversized JSON body that inflates before HMAC check to `validate` in `flow/validate.ts` such that detectWebhookType mis-selects a validation branch for an oversized JSON body that inflates before HMAC check, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/flow/validate.ts` -> `validate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an oversized JSON body that inflates before HMAC check
- Exploit idea: detectWebhookType mis-selects a validation branch for an oversized JSON body that inflates before HMAC check
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
