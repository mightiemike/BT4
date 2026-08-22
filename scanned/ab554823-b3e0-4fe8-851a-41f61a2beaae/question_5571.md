# Q5571: customer-account/authenticate — liquid-body injection

## Question
Can an unprivileged attacker submit a preflight that primes a cached authenticated response to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that processLiquidBody handles a preflight that primes a cached authenticated response unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a preflight that primes a cached authenticated response
- Exploit idea: processLiquidBody handles a preflight that primes a cached authenticated response unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
