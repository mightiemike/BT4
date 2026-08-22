# Q2666: middlewares/validate-authenticated-session — shop-from-header trust

## Question
Can an unprivileged attacker submit a request whose shop is derived from an untrusted header to `validateWithTokenExchange` in `middlewares/validate-authenticated-session.ts` such that validateWithTokenExchange derives shop from a request whose shop is derived from an untrusted header (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithTokenExchange`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request whose shop is derived from an untrusted header
- Exploit idea: validateWithTokenExchange derives shop from a request whose shop is derived from an untrusted header (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
