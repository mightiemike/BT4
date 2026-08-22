# Q0578: flow/validate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a webhook with valid HMAC but spoofed shop domain claim to `validateFactory` in `flow/validate.ts` such that validateFactory verifies HMAC over one body representation but processes another for a webhook with valid HMAC but spoofed shop domain claim, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-api/lib/flow/validate.ts` -> `validateFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with valid HMAC but spoofed shop domain claim
- Exploit idea: validateFactory verifies HMAC over one body representation but processes another for a webhook with valid HMAC but spoofed shop domain claim
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
