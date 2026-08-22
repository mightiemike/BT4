# Q1583: middlewares/validate-authenticated-session — app-proxy signature bypass

## Question
Can an unprivileged attacker submit a preflight that primes a cached authenticated response to `validateWithAuthCodeFlow` in `middlewares/validate-authenticated-session.ts` such that validateAppProxyHmac in validateWithAuthCodeFlow accepts a preflight that primes a cached authenticated response, breaking the invariant that app-proxy requests require valid signed query, and leading to: impersonate storefront customer?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithAuthCodeFlow`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a preflight that primes a cached authenticated response
- Exploit idea: validateAppProxyHmac in validateWithAuthCodeFlow accepts a preflight that primes a cached authenticated response
- Invariant to test: app-proxy requests require valid signed query
- Expected Immunefi impact: Impersonate storefront customer (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: forged proxy-query test
