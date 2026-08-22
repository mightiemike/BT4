# Q2606: checkout/authenticate — shop-from-header trust

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `authenticateCheckoutFactory` in `checkout/authenticate.ts` such that authenticateCheckoutFactory derives shop from an OPTIONS/CORS preflight abused to leak headers (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/checkout/authenticate.ts` -> `authenticateCheckoutFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: authenticateCheckoutFactory derives shop from an OPTIONS/CORS preflight abused to leak headers (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
