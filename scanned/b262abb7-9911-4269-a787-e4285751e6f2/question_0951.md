# Q0951: admin/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit an app-proxy request with a forged signature query to `authStrategyFactory` in `admin/authenticate.ts` such that validateAppProxyHmac in authStrategyFactory accepts an app-proxy request with a forged signature query, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `authStrategyFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy request with a forged signature query
- Exploit idea: validateAppProxyHmac in authStrategyFactory accepts an app-proxy request with a forged signature query
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
