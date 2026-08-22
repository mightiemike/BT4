# Q0638: webhooks/authenticate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a chunked/streamed body read twice to `authenticate` in `webhooks/authenticate.ts` such that authenticate verifies HMAC over one body representation but processes another for a chunked/streamed body read twice, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a chunked/streamed body read twice
- Exploit idea: authenticate verifies HMAC over one body representation but processes another for a chunked/streamed body read twice
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
