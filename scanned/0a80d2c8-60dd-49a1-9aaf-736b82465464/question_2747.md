# Q2747: webhooks/authenticate — replay of delivery

## Question
Can an unprivileged attacker submit a body re-encoded so JSON.parse and the signed bytes diverge to `authenticateWebhookFactory` in `webhooks/authenticate.ts` such that authenticateWebhookFactory lacks delivery-id idempotency, accepting a body re-encoded so JSON.parse and the signed bytes diverge, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticateWebhookFactory`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a body re-encoded so JSON.parse and the signed bytes diverge
- Exploit idea: authenticateWebhookFactory lacks delivery-id idempotency, accepting a body re-encoded so JSON.parse and the signed bytes diverge
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
