# Q3370: lib/webhooks/process — handler dispatch bypass

## Question
Can an unprivileged attacker submit a request missing one required webhook header to `handleInvalidWebhook` in `lib/webhooks/process.ts` such that process/callWebhookHandlers routes a request missing one required webhook header to a handler without full validation, breaking the invariant that dispatch happens only post-verification, and leading to: forged handler invocation?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `handleInvalidWebhook`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a request missing one required webhook header
- Exploit idea: process/callWebhookHandlers routes a request missing one required webhook header to a handler without full validation
- Invariant to test: dispatch happens only post-verification
- Expected Immunefi impact: Forged handler invocation (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert handler count 0 for bad HMAC
