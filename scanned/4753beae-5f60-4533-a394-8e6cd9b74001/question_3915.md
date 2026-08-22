# Q3915: admin/authenticate — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a request forging the embedded host param to `addEmbeddedFeatures` in `admin/authenticate.ts` such that addEmbeddedFeatures accepts a request forging the embedded host param from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `addEmbeddedFeatures`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request forging the embedded host param
- Exploit idea: addEmbeddedFeatures accepts a request forging the embedded host param from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
