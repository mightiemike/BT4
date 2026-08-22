# Q4623: webhooks/validate — oversized-body DoS

## Question
Can an unprivileged attacker submit a chunked/streamed body read twice to `checkWebhookHeaders` in `webhooks/validate.ts` such that checkWebhookHeaders buffers/inflates a chunked/streamed body read twice before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkWebhookHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a chunked/streamed body read twice
- Exploit idea: checkWebhookHeaders buffers/inflates a chunked/streamed body read twice before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
