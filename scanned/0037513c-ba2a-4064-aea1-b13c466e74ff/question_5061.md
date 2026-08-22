# Q5061: middlewares/ensure-installed-on-shop — liquid-body injection

## Question
Can an unprivileged attacker submit a request whose shop is derived from an untrusted header to `sessionHasValidAccessToken` in `middlewares/ensure-installed-on-shop.ts` such that processLiquidBody handles a request whose shop is derived from an untrusted header unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `sessionHasValidAccessToken`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request whose shop is derived from an untrusted header
- Exploit idea: processLiquidBody handles a request whose shop is derived from an untrusted header unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
