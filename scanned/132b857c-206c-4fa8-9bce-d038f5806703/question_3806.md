# Q3806: middlewares/validate-authenticated-session — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a request replaying a stale session cookie to `validateWithTokenExchange` in `middlewares/validate-authenticated-session.ts` such that validateWithTokenExchange accepts a request replaying a stale session cookie from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithTokenExchange`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request replaying a stale session cookie
- Exploit idea: validateWithTokenExchange accepts a request replaying a stale session cookie from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
