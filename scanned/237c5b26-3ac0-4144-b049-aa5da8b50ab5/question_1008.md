# Q1008: admin/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `respondToBouncePageRequest` in `admin/authenticate.ts` such that validateAppProxyHmac in respondToBouncePageRequest accepts an OPTIONS/CORS preflight abused to leak headers, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `respondToBouncePageRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: validateAppProxyHmac in respondToBouncePageRequest accepts an OPTIONS/CORS preflight abused to leak headers
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
