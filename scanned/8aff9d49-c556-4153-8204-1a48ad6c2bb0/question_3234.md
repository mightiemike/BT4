# Q3234: customer-account/authenticate — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a request with no session token but a valid-looking shop param to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that authenticateCustomerAccountFactory accepts a request with no session token but a valid-looking shop param from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request with no session token but a valid-looking shop param
- Exploit idea: authenticateCustomerAccountFactory accepts a request with no session token but a valid-looking shop param from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
