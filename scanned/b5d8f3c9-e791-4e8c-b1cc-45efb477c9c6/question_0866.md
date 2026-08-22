# Q0866: webhooks/authenticate — missing-header fallthrough

## Question
Can an unprivileged attacker submit a valid-looking body with a mismatched raw vs parsed representation to `authenticate` in `webhooks/authenticate.ts` such that authenticate proceeds when a required webhook header is absent for a valid-looking body with a mismatched raw vs parsed representation, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a valid-looking body with a mismatched raw vs parsed representation
- Exploit idea: authenticate proceeds when a required webhook header is absent for a valid-looking body with a mismatched raw vs parsed representation
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
