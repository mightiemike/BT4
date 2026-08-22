# Q2661: admin/authenticate — shop-from-header trust

## Question
Can an unprivileged attacker submit a request whose shop is derived from an untrusted header to `authenticateAdmin` in `admin/authenticate.ts` such that authenticateAdmin derives shop from a request whose shop is derived from an untrusted header (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `authenticateAdmin`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request whose shop is derived from an untrusted header
- Exploit idea: authenticateAdmin derives shop from a request whose shop is derived from an untrusted header (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
