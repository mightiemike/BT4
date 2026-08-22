# Q4343: webhooks/authenticate — oversized-body DoS

## Question
Can an unprivileged attacker submit a body re-encoded so JSON.parse and the signed bytes diverge to `authenticateWebhookFactory` in `webhooks/authenticate.ts` such that authenticateWebhookFactory buffers/inflates a body re-encoded so JSON.parse and the signed bytes diverge before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticateWebhookFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a body re-encoded so JSON.parse and the signed bytes diverge
- Exploit idea: authenticateWebhookFactory buffers/inflates a body re-encoded so JSON.parse and the signed bytes diverge before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
