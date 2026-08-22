# Q5783: customer-account/authenticate — options/cors leak

## Question
Can an unprivileged attacker submit a customer-account/checkout token from a different session to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that respondToOptionsRequest/CORS for a customer-account/checkout token from a different session leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a customer-account/checkout token from a different session
- Exploit idea: respondToOptionsRequest/CORS for a customer-account/checkout token from a different session leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
