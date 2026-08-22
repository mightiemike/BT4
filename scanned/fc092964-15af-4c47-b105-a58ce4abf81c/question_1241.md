# Q1241: middlewares/validate-authenticated-session — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `validateWithTokenExchange` in `middlewares/validate-authenticated-session.ts` such that validateAppProxyHmac in validateWithTokenExchange accepts a document vs XHR request type mismatch, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithTokenExchange`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: validateAppProxyHmac in validateWithTokenExchange accepts a document vs XHR request type mismatch
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
