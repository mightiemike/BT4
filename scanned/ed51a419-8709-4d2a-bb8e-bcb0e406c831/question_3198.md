# Q3198: webhooks/validate — handler dispatch bypass

## Question
Can an unprivileged attacker submit a forged X-Shopify-Hmac-Sha256 header with a truncated digest to `validateFactory` in `webhooks/validate.ts` such that process/callWebhookHandlers routes a forged X-Shopify-Hmac-Sha256 header with a truncated digest to a handler without full validation, breaking the invariant that dispatch happens only post-verification, and leading to: forged handler invocation?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `validateFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Exploit idea: process/callWebhookHandlers routes a forged X-Shopify-Hmac-Sha256 header with a truncated digest to a handler without full validation
- Invariant to test: dispatch happens only post-verification
- Expected Immunefi impact: Forged handler invocation (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert handler count 0 for bad HMAC
