# Q4283: flow/validate — oversized-body DoS

## Question
Can an unprivileged attacker submit an oversized JSON body that inflates before HMAC check to `validate` in `flow/validate.ts` such that validate buffers/inflates an oversized JSON body that inflates before HMAC check before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/flow/validate.ts` -> `validate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an oversized JSON body that inflates before HMAC check
- Exploit idea: validate buffers/inflates an oversized JSON body that inflates before HMAC check before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
