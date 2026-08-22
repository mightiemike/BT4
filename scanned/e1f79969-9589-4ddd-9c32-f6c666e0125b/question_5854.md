# Q5854: middlewares/ensure-installed-on-shop — options/cors leak

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `getRequestShop` in `middlewares/ensure-installed-on-shop.ts` such that respondToOptionsRequest/CORS for a document vs XHR request type mismatch leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `getRequestShop`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: respondToOptionsRequest/CORS for a document vs XHR request type mismatch leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
