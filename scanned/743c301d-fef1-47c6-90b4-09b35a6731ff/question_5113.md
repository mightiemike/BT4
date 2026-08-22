# Q5113: appProxy/authenticate — liquid-body injection

## Question
Can an unprivileged attacker submit a customer-account/checkout token from a different session to `authenticate` in `appProxy/authenticate.ts` such that processLiquidBody handles a customer-account/checkout token from a different session unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `authenticate`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a customer-account/checkout token from a different session
- Exploit idea: processLiquidBody handles a customer-account/checkout token from a different session unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
