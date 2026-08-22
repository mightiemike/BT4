# Q4395: webhooks/validate — oversized-body DoS

## Question
Can an unprivileged attacker submit an unexpected content-type on the webhook POST to `validateFactory` in `webhooks/validate.ts` such that validateFactory buffers/inflates an unexpected content-type on the webhook POST before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `validateFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an unexpected content-type on the webhook POST
- Exploit idea: validateFactory buffers/inflates an unexpected content-type on the webhook POST before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
