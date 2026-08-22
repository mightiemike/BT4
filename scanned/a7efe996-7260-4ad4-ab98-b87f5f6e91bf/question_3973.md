# Q3973: appProxy/authenticate — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a preflight that primes a cached authenticated response to `authenticate` in `appProxy/authenticate.ts` such that authenticate accepts a preflight that primes a cached authenticated response from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `authenticate`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a preflight that primes a cached authenticated response
- Exploit idea: authenticate accepts a preflight that primes a cached authenticated response from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
