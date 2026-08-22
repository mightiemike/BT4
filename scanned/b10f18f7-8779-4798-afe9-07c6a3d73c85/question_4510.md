# Q4510: lib/webhooks/process — oversized-body DoS

## Question
Can an unprivileged attacker submit a Flow/fulfillment payload with attacker-chosen fields to `callWebhookHandlers` in `lib/webhooks/process.ts` such that callWebhookHandlers buffers/inflates a Flow/fulfillment payload with attacker-chosen fields before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `callWebhookHandlers`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a Flow/fulfillment payload with attacker-chosen fields
- Exploit idea: callWebhookHandlers buffers/inflates a Flow/fulfillment payload with attacker-chosen fields before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
