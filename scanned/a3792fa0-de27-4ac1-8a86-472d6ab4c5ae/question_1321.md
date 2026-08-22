# Q1321: src/webhooks/process — missing-header fallthrough

## Question
Can an unprivileged attacker submit a Flow/fulfillment payload with attacker-chosen fields to `process` in `src/webhooks/process.ts` such that process proceeds when a required webhook header is absent for a Flow/fulfillment payload with attacker-chosen fields, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-app-express/src/webhooks/process.ts` -> `process`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a Flow/fulfillment payload with attacker-chosen fields
- Exploit idea: process proceeds when a required webhook header is absent for a Flow/fulfillment payload with attacker-chosen fields
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
