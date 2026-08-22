# Q1123: appProxy/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a customer-account/checkout token from a different session to `processLiquidBody` in `appProxy/authenticate.ts` such that validateAppProxyHmac in processLiquidBody accepts a customer-account/checkout token from a different session, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `processLiquidBody`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a customer-account/checkout token from a different session
- Exploit idea: validateAppProxyHmac in processLiquidBody accepts a customer-account/checkout token from a different session
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
