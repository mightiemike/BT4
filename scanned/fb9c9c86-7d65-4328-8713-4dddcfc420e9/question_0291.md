# Q0291: webhooks/validate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit an oversized JSON body that inflates before HMAC check to `checkWebhooksHeaders` in `webhooks/validate.ts` such that checkWebhooksHeaders verifies HMAC over one body representation but processes another for an oversized JSON body that inflates before HMAC check, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkWebhooksHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an oversized JSON body that inflates before HMAC check
- Exploit idea: checkWebhooksHeaders verifies HMAC over one body representation but processes another for an oversized JSON body that inflates before HMAC check
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
