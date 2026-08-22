# Q4794: webhooks/validate — type-detection confusion

## Question
Can an unprivileged attacker submit a forged X-Shopify-Hmac-Sha256 header with a truncated digest to `validateFactory` in `webhooks/validate.ts` such that detectWebhookType mis-selects a validation branch for a forged X-Shopify-Hmac-Sha256 header with a truncated digest, breaking the invariant that webhook type detection cannot be attacker-steered into a weaker path, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `validateFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Exploit idea: detectWebhookType mis-selects a validation branch for a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Invariant to test: webhook type detection cannot be attacker-steered into a weaker path
- Expected Immunefi impact: Auth bypass (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: craft ambiguous headers, assert strict typing
