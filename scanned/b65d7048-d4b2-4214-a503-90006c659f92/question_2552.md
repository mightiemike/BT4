# Q2552: middlewares/validate-authenticated-session — shop-from-header trust

## Question
Can an unprivileged attacker submit an app-proxy request with a forged signature query to `setShopFromSessionOrToken` in `middlewares/validate-authenticated-session.ts` such that setShopFromSessionOrToken derives shop from an app-proxy request with a forged signature query (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `setShopFromSessionOrToken`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy request with a forged signature query
- Exploit idea: setShopFromSessionOrToken derives shop from an app-proxy request with a forged signature query (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
