# Q0183: flow/authenticate — raw-vs-parsed body divergence

## Question
Can an unprivileged attacker submit a request missing one required webhook header to `authenticate` in `flow/authenticate.ts` such that authenticate verifies HMAC over one body representation but processes another for a request missing one required webhook header, breaking the invariant that signed bytes equal processed bytes, and leading to: forged webhook triggers state change/data op?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/flow/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a request missing one required webhook header
- Exploit idea: authenticate verifies HMAC over one body representation but processes another for a request missing one required webhook header
- Invariant to test: signed bytes equal processed bytes
- Expected Immunefi impact: Forged webhook triggers state change/data op (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: send body where raw!=JSON.parse and assert reject
