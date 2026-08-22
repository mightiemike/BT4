# Q0520: lib/webhooks/process — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a Flow/fulfillment payload with attacker-chosen fields to `process` in `lib/webhooks/process.ts` such that process verifies HMAC over one body representation but processes another for a Flow/fulfillment payload with attacker-chosen fields, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `process`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a Flow/fulfillment payload with attacker-chosen fields
- Exploit idea: process verifies HMAC over one body representation but processes another for a Flow/fulfillment payload with attacker-chosen fields
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
