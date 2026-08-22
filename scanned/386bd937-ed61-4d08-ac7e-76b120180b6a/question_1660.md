# Q1660: lib/webhooks/process — shop-domain spoof

## Question
Can an unprivileged attacker submit a valid-looking body with a mismatched raw vs parsed representation to `handleInvalidWebhook` in `lib/webhooks/process.ts` such that handleInvalidWebhook trusts the shop/topic header claim rather than the signed origin, breaking the invariant that shop identity bound to verified signature, and leading to: cross-tenant webhook action?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/process.ts` -> `handleInvalidWebhook`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a valid-looking body with a mismatched raw vs parsed representation
- Exploit idea: handleInvalidWebhook trusts the shop/topic header claim rather than the signed origin
- Invariant to test: shop identity bound to verified signature
- Expected Immunefi impact: Cross-tenant webhook action (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: valid-HMAC-wrong-shop test
