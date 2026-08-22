# Q4888: helpers/reject-bot-request — liquid-body injection

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that processLiquidBody handles a bot user-agent that should be short-circuited unsafely, breaking the invariant that proxied body is not evaluated with app trust, and leading to: injection in app-proxy response?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: processLiquidBody handles a bot user-agent that should be short-circuited unsafely
- Invariant to test: proxied body is not evaluated with app trust
- Expected Immunefi impact: Injection in app-proxy response (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malicious-liquid test
