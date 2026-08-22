# Q1237: appProxy/authenticate — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `authenticate` in `appProxy/authenticate.ts` such that validateAppProxyHmac in authenticate accepts a document vs XHR request type mismatch, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `authenticate`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: validateAppProxyHmac in authenticate accepts a document vs XHR request type mismatch
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
