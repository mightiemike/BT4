# Q5173: helpers/reject-bot-request — liquid-body injection

## Question
Can an unprivileged attacker submit a request that skips the embedded/installed gate to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that processLiquidBody handles a request that skips the embedded/installed gate unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request that skips the embedded/installed gate
- Exploit idea: processLiquidBody handles a request that skips the embedded/installed gate unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
