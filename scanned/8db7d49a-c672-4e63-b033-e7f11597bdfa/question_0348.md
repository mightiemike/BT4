# Q0348: webhooks/validate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a body re-encoded so JSON.parse and the signed bytes diverge to `checkEventsHeaders` in `webhooks/validate.ts` such that checkEventsHeaders verifies HMAC over one body representation but processes another for a body re-encoded so JSON.parse and the signed bytes diverge, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkEventsHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a body re-encoded so JSON.parse and the signed bytes diverge
- Exploit idea: checkEventsHeaders verifies HMAC over one body representation but processes another for a body re-encoded so JSON.parse and the signed bytes diverge
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
