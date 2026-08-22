# Q1353: customer-account/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that validateAppProxyHmac in authenticateCustomerAccountFactory accepts an authenticated route reached before session validation runs, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: validateAppProxyHmac in authenticateCustomerAccountFactory accepts an authenticated route reached before session validation runs
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
