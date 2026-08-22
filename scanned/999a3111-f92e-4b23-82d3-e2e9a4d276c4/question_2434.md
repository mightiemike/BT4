# Q2434: appProxy/authenticate — shop-from-header trust

## Question
Can an unprivileged attacker submit a request with no session token but a valid-looking shop param to `validateAppProxyHmac` in `appProxy/authenticate.ts` such that validateAppProxyHmac derives shop from a request with no session token but a valid-looking shop param (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `validateAppProxyHmac`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request with no session token but a valid-looking shop param
- Exploit idea: validateAppProxyHmac derives shop from a request with no session token but a valid-looking shop param (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
