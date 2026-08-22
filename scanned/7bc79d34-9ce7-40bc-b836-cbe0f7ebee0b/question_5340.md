# Q5340: admin/authenticate — liquid-body injection

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `addScopesFeatures` in `admin/authenticate.ts` such that processLiquidBody handles an authenticated route reached before session validation runs unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `addScopesFeatures`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: processLiquidBody handles an authenticated route reached before session validation runs unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
