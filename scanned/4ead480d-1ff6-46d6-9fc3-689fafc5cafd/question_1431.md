# Q1431: webhooks/validate — missing-header fallthrough

## Question
Can an unprivileged attacker submit a chunked/streamed body read twice to `checkWebhookHeaders` in `webhooks/validate.ts` such that checkWebhookHeaders proceeds when a required webhook header is absent for a chunked/streamed body read twice, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkWebhookHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a chunked/streamed body read twice
- Exploit idea: checkWebhookHeaders proceeds when a required webhook header is absent for a chunked/streamed body read twice
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
