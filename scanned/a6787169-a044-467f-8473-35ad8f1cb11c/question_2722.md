# Q2722: helpers/reject-bot-request — shop-from-header trust

## Question
Can an unprivileged attacker submit a customer-account/checkout token from a different session to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that respondToBotRequest derives shop from a customer-account/checkout token from a different session (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a customer-account/checkout token from a different session
- Exploit idea: respondToBotRequest derives shop from a customer-account/checkout token from a different session (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
