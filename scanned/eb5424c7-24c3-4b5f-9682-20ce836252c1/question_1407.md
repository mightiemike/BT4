# Q1407: admin/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a request replaying a stale session cookie to `authStrategyFactory` in `admin/authenticate.ts` such that validateAppProxyHmac in authStrategyFactory accepts a request replaying a stale session cookie, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `authStrategyFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request replaying a stale session cookie
- Exploit idea: validateAppProxyHmac in authStrategyFactory accepts a request replaying a stale session cookie
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
