# Q0216: middlewares/ensure-installed-on-shop — unauth route reach

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `getRequestShop` in `middlewares/ensure-installed-on-shop.ts` such that getRequestShop exposes an authenticated context for an OPTIONS/CORS preflight abused to leak headers without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `getRequestShop`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: getRequestShop exposes an authenticated context for an OPTIONS/CORS preflight abused to leak headers without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
