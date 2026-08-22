# Q3345: admin/authenticate — customer/checkout token reuse

## Question
Can an unprivileged attacker submit an app-proxy request with a forged signature query to `respondToExitIframeRequest` in `admin/authenticate.ts` such that respondToExitIframeRequest accepts an app-proxy request with a forged signature query from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `respondToExitIframeRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy request with a forged signature query
- Exploit idea: respondToExitIframeRequest accepts an app-proxy request with a forged signature query from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
