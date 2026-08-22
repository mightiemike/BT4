# Q3028: lib/webhooks/process — replay of delivery

## Question
Can an unprivileged attacker submit a chunked/streamed body read twice to `handleInvalidWebhook` in `lib/webhooks/process.ts` such that handleInvalidWebhook lacks delivery-id idempotency, accepting a chunked/streamed body read twice, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `handleInvalidWebhook`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a chunked/streamed body read twice
- Exploit idea: handleInvalidWebhook lacks delivery-id idempotency, accepting a chunked/streamed body read twice
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
