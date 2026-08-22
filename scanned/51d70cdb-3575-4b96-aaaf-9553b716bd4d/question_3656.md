# Q3656: flow/validate — handler dispatch bypass

## Question
Can an unprivileged attacker submit an api-version header that selects a different validation path to `validateFactory` in `flow/validate.ts` such that process/callWebhookHandlers routes an api-version header that selects a different validation path to a handler without full validation, breaking the invariant that dispatch happens only post-verification, and leading to: forged handler invocation?

## Target
- File/function: `packages/apps/shopify-api/lib/flow/validate.ts` -> `validateFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an api-version header that selects a different validation path
- Exploit idea: process/callWebhookHandlers routes an api-version header that selects a different validation path to a handler without full validation
- Invariant to test: dispatch happens only post-verification
- Expected Immunefi impact: Forged handler invocation (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert handler count 0 for bad HMAC
