# Q2889: admin/authenticate — shop-from-header trust

## Question
Can an unprivileged attacker submit an app-proxy logged-in-customer id claim under attacker control to `respondToExitIframeRequest` in `admin/authenticate.ts` such that respondToExitIframeRequest derives shop from an app-proxy logged-in-customer id claim under attacker control (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `respondToExitIframeRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy logged-in-customer id claim under attacker control
- Exploit idea: respondToExitIframeRequest derives shop from an app-proxy logged-in-customer id claim under attacker control (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
