# Q4001: webhooks/authenticate — oversized-body DoS

## Question
Can an unprivileged attacker submit a forged X-Shopify-Hmac-Sha256 header with a truncated digest to `authenticateWebhookFactory` in `webhooks/authenticate.ts` such that authenticateWebhookFactory buffers/inflates a forged X-Shopify-Hmac-Sha256 header with a truncated digest before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticateWebhookFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Exploit idea: authenticateWebhookFactory buffers/inflates a forged X-Shopify-Hmac-Sha256 header with a truncated digest before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
