# Q0066: fulfillment-service/validate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a valid-looking body with a mismatched raw vs parsed representation to `validate` in `fulfillment-service/validate.ts` such that validate verifies HMAC over one body representation but processes another for a valid-looking body with a mismatched raw vs parsed representation, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` -> `validate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a valid-looking body with a mismatched raw vs parsed representation
- Exploit idea: validate verifies HMAC over one body representation but processes another for a valid-looking body with a mismatched raw vs parsed representation
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
