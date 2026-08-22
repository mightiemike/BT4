# Q1185: middlewares/ensure-installed-on-shop — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a request that skips the embedded/installed gate to `ensureInstalledOnShop` in `middlewares/ensure-installed-on-shop.ts` such that validateAppProxyHmac in ensureInstalledOnShop accepts a request that skips the embedded/installed gate, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `ensureInstalledOnShop`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request that skips the embedded/installed gate
- Exploit idea: validateAppProxyHmac in ensureInstalledOnShop accepts a request that skips the embedded/installed gate
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
