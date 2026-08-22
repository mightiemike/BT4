# Q4570: src/webhooks/process — oversized-body DoS

## Question
Can an unprivileged attacker submit a webhook with valid HMAC but spoofed shop domain claim to `process` in `src/webhooks/process.ts` such that process buffers/inflates a webhook with valid HMAC but spoofed shop domain claim before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-app-express/src/webhooks/process.ts` -> `process`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with valid HMAC but spoofed shop domain claim
- Exploit idea: process buffers/inflates a webhook with valid HMAC but spoofed shop domain claim before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
