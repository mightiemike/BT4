# Q5001: customer-account/authenticate — liquid-body injection

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that processLiquidBody handles an OPTIONS/CORS preflight abused to leak headers unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: processLiquidBody handles an OPTIONS/CORS preflight abused to leak headers unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
