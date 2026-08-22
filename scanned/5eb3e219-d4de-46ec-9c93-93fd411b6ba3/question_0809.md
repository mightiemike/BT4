# Q0809: webhooks/authenticate — missing-header fallthrough

## Question
Can an unprivileged attacker submit a forged X-Shopify-Hmac-Sha256 header with a truncated digest to `authenticateWebhookFactory` in `webhooks/authenticate.ts` such that authenticateWebhookFactory proceeds when a required webhook header is absent for a forged X-Shopify-Hmac-Sha256 header with a truncated digest, breaking the invariant that all required headers present before trust, and leading to: unauthenticated webhook processing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticateWebhookFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Exploit idea: authenticateWebhookFactory proceeds when a required webhook header is absent for a forged X-Shopify-Hmac-Sha256 header with a truncated digest
- Invariant to test: all required headers present before trust
- Expected Immunefi impact: Unauthenticated webhook processing (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: omit each header and assert 401/handler-not-called
