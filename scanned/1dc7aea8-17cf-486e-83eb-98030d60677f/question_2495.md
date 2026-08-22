# Q2495: middlewares/validate-authenticated-session — shop-from-header trust

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `handleSessionError` in `middlewares/validate-authenticated-session.ts` such that handleSessionError derives shop from a bot user-agent that should be short-circuited (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `handleSessionError`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: handleSessionError derives shop from a bot user-agent that should be short-circuited (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
