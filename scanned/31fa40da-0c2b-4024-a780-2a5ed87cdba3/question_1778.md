# Q1778: webhooks/authenticate — shop-domain spoof

## Question
Can an unprivileged attacker submit a request missing one required webhook header to `authenticate` in `webhooks/authenticate.ts` such that authenticate trusts the shop/topic header claim rather than the signed origin, breaking the invariant that shop identity bound to verified signature, and leading to: cross-tenant webhook action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` -> `authenticate`
- Entrypoint: POST to the app's webhook/Flow/fulfillment endpoint
- Attacker controls: a request missing one required webhook header
- Exploit idea: authenticate trusts the shop/topic header claim rather than the signed origin
- Invariant to test: shop identity bound to verified signature
- Expected Immunefi impact: Cross-tenant webhook action (In scope: forged authenticated request causing state change/data access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: valid-HMAC-wrong-shop test
