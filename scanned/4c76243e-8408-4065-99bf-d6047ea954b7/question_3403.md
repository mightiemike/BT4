# Q3403: appProxy/authenticate — customer/checkout token reuse

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `processLiquidBody` in `appProxy/authenticate.ts` such that processLiquidBody accepts an OPTIONS/CORS preflight abused to leak headers from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `processLiquidBody`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: processLiquidBody accepts an OPTIONS/CORS preflight abused to leak headers from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
