# Q3141: webhooks/validate — replay of delivery

## Question
Can an unprivileged attacker submit a payload whose HMAC is valid for an empty body to `checkEventsHeaders` in `webhooks/validate.ts` such that checkEventsHeaders lacks delivery-id idempotency, accepting a payload whose HMAC is valid for an empty body, breaking the invariant that each delivery processed at most once where it mutates state, and leading to: duplicated side effects?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkEventsHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a payload whose HMAC is valid for an empty body
- Exploit idea: checkEventsHeaders lacks delivery-id idempotency, accepting a payload whose HMAC is valid for an empty body
- Invariant to test: each delivery processed at most once where it mutates state
- Expected Immunefi impact: Duplicated side effects (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: replay same signed webhook twice
