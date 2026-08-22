# Q2952: middlewares/ensure-installed-on-shop — shop-from-header trust

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `getRequestShop` in `middlewares/ensure-installed-on-shop.ts` such that getRequestShop derives shop from an authenticated route reached before session validation runs (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `getRequestShop`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: getRequestShop derives shop from an authenticated route reached before session validation runs (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
