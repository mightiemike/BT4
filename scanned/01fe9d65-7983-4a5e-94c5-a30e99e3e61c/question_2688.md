# Q2688: fulfillment-service/validate — replay of delivery

## Question
Can an unprivileged attacker submit an oversized JSON body that inflates before HMAC check to `validate` in `fulfillment-service/validate.ts` such that validate lacks delivery-id idempotency, accepting an oversized JSON body that inflates before HMAC check, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` -> `validate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: an oversized JSON body that inflates before HMAC check
- Exploit idea: validate lacks delivery-id idempotency, accepting an oversized JSON body that inflates before HMAC check
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
