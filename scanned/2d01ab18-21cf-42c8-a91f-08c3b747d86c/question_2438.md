# Q2438: middlewares/validate-authenticated-session — shop-from-header trust

## Question
Can an unprivileged attacker submit a request with no session token but a valid-looking shop param to `validateWithAuthCodeFlow` in `middlewares/validate-authenticated-session.ts` such that validateWithAuthCodeFlow derives shop from a request with no session token but a valid-looking shop param (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithAuthCodeFlow`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request with no session token but a valid-looking shop param
- Exploit idea: validateWithAuthCodeFlow derives shop from a request with no session token but a valid-looking shop param (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
