# Q5283: admin/authenticate — liquid-body injection

## Question
Can an unprivileged attacker submit an app-proxy logged-in-customer id claim under attacker control to `addEmbeddedFeatures` in `admin/authenticate.ts` such that processLiquidBody handles an app-proxy logged-in-customer id claim under attacker control unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `addEmbeddedFeatures`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy logged-in-customer id claim under attacker control
- Exploit idea: processLiquidBody handles an app-proxy logged-in-customer id claim under attacker control unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
