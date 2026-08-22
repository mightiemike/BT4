# Q3179: middlewares/validate-authenticated-session — shop-from-header trust

## Question
Can an unprivileged attacker submit a preflight that primes a cached authenticated response to `validateAuthenticatedSession` in `middlewares/validate-authenticated-session.ts` such that validateAuthenticatedSession derives shop from a preflight that primes a cached authenticated response (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateAuthenticatedSession`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a preflight that primes a cached authenticated response
- Exploit idea: validateAuthenticatedSession derives shop from a preflight that primes a cached authenticated response (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
