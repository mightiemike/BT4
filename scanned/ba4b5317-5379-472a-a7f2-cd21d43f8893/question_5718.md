# Q5718: middlewares/ensure-installed-on-shop — options/cors leak

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `embedAppIntoShopify` in `middlewares/ensure-installed-on-shop.ts` such that respondToOptionsRequest/CORS for an OPTIONS/CORS preflight abused to leak headers leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `embedAppIntoShopify`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: respondToOptionsRequest/CORS for an OPTIONS/CORS preflight abused to leak headers leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
