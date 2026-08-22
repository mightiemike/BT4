# Q4056: fulfillment-service/validate — oversized-body DoS

## Question
Can an unprivileged attacker submit a valid-looking body with a mismatched raw vs parsed representation to `validate` in `fulfillment-service/validate.ts` such that validate buffers/inflates a valid-looking body with a mismatched raw vs parsed representation before verifying HMAC, breaking the invariant that bounded work before authentication, and leading to: dos of webhook endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` -> `validate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a valid-looking body with a mismatched raw vs parsed representation
- Exploit idea: validate buffers/inflates a valid-looking body with a mismatched raw vs parsed representation before verifying HMAC
- Invariant to test: bounded work before authentication
- Expected Immunefi impact: DoS of webhook endpoint (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: large-body timing/memory test
