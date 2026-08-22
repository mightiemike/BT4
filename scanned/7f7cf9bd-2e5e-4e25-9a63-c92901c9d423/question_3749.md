# Q3749: middlewares/validate-authenticated-session — customer/checkout token reuse

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `validateAuthenticatedSession` in `middlewares/validate-authenticated-session.ts` such that validateAuthenticatedSession accepts an authenticated route reached before session validation runs from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateAuthenticatedSession`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: validateAuthenticatedSession accepts an authenticated route reached before session validation runs from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
