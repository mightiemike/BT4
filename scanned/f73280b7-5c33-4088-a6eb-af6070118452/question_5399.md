# Q5399: checkout/authenticate — liquid-body injection

## Question
Can an unprivileged attacker submit a request replaying a stale session cookie to `authenticateCheckoutFactory` in `checkout/authenticate.ts` such that processLiquidBody handles a request replaying a stale session cookie unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/checkout/authenticate.ts` -> `authenticateCheckoutFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request replaying a stale session cookie
- Exploit idea: processLiquidBody handles a request replaying a stale session cookie unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
