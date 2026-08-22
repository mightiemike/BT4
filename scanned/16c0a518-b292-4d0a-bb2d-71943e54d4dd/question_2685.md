# Q2685: webhooks/validate — replay of delivery

## Question
Can an unprivileged attacker submit an oversized JSON body that inflates before HMAC check to `checkWebhooksHeaders` in `webhooks/validate.ts` such that checkWebhooksHeaders lacks delivery-id idempotency, accepting an oversized JSON body that inflates before HMAC check, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkWebhooksHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an oversized JSON body that inflates before HMAC check
- Exploit idea: checkWebhooksHeaders lacks delivery-id idempotency, accepting an oversized JSON body that inflates before HMAC check
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
