# Q1093: src/webhooks/process — missing-header fallthrough

## Question
Can an unprivileged attacker submit an oversized JSON body that inflates before HMAC check to `process` in `src/webhooks/process.ts` such that process proceeds when a required webhook header is absent for an oversized JSON body that inflates before HMAC check, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-app-express/src/webhooks/process.ts` -> `process`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an oversized JSON body that inflates before HMAC check
- Exploit idea: process proceeds when a required webhook header is absent for an oversized JSON body that inflates before HMAC check
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
