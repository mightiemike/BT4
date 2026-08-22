# Q1470: middlewares/ensure-installed-on-shop — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a crafted Accept/Sec-Fetch header steering the auth branch to `deleteAppInstallationHandler` in `middlewares/ensure-installed-on-shop.ts` such that validateAppProxyHmac in deleteAppInstallationHandler accepts a crafted Accept/Sec-Fetch header steering the auth branch, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `deleteAppInstallationHandler`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a crafted Accept/Sec-Fetch header steering the auth branch
- Exploit idea: validateAppProxyHmac in deleteAppInstallationHandler accepts a crafted Accept/Sec-Fetch header steering the auth branch
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
