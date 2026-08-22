# Q1068: customer-account/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a request whose shop is derived from an untrusted header to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that validateAppProxyHmac in authenticateCustomerAccountFactory accepts a request whose shop is derived from an untrusted header, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request whose shop is derived from an untrusted header
- Exploit idea: validateAppProxyHmac in authenticateCustomerAccountFactory accepts a request whose shop is derived from an untrusted header
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
