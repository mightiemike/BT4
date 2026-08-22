# Q0895: appProxy/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `processLiquidBody` in `appProxy/authenticate.ts` such that validateAppProxyHmac in processLiquidBody accepts a bot user-agent that should be short-circuited, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `processLiquidBody`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: validateAppProxyHmac in processLiquidBody accepts a bot user-agent that should be short-circuited
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
