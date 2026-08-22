# Q2286: webhooks/validate — shop-domain spoof

## Question
Can an unprivileged attacker submit a webhook with an extra unsigned trailing header to `checkWebhooksHeaders` in `webhooks/validate.ts` such that checkWebhooksHeaders trusts the shop/topic header claim rather than the signed origin, breaking the invariant that shop identity bound to verified signature, and leading to: cross-tenant webhook action?

## Target
- File/function: `packages/apps/shopify-api/lib/webhooks/validate.ts` -> `checkWebhooksHeaders`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a webhook with an extra unsigned trailing header
- Exploit idea: checkWebhooksHeaders trusts the shop/topic header claim rather than the signed origin
- Invariant to test: shop identity bound to verified signature
- Expected Immunefi impact: Cross-tenant webhook action (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: valid-HMAC-wrong-shop test
