# Q1523: checkout/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a request forging the embedded host param to `authenticateCheckoutFactory` in `checkout/authenticate.ts` such that validateAppProxyHmac in authenticateCheckoutFactory accepts a request forging the embedded host param, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/checkout/authenticate.ts` -> `authenticateCheckoutFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request forging the embedded host param
- Exploit idea: validateAppProxyHmac in authenticateCheckoutFactory accepts a request forging the embedded host param
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
