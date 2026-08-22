# Q2458: lib/webhooks/process — replay of delivery

## Question
Can an unprivileged attacker submit a valid-looking body with a mismatched raw vs parsed representation to `callWebhookHandlers` in `lib/webhooks/process.ts` such that callWebhookHandlers lacks delivery-id idempotency, accepting a valid-looking body with a mismatched raw vs parsed representation, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `callWebhookHandlers`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a valid-looking body with a mismatched raw vs parsed representation
- Exploit idea: callWebhookHandlers lacks delivery-id idempotency, accepting a valid-looking body with a mismatched raw vs parsed representation
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
