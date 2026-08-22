# Q5953: customer-account/authenticate — options/cors leak

## Question
Can an unprivileged attacker submit a request replaying a stale session cookie to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that respondToOptionsRequest/CORS for a request replaying a stale session cookie leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request replaying a stale session cookie
- Exploit idea: respondToOptionsRequest/CORS for a request replaying a stale session cookie leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
