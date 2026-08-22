# Q4319: middlewares/validate-authenticated-session — installed-gate skip

## Question
Can an unprivileged attacker submit a customer-account/checkout token from a different session to `validateAuthenticatedSession` in `middlewares/validate-authenticated-session.ts` such that ensureInstalled/validateAuthenticatedSession serves a customer-account/checkout token from a different session without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateAuthenticatedSession`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a customer-account/checkout token from a different session
- Exploit idea: ensureInstalled/validateAuthenticatedSession serves a customer-account/checkout token from a different session without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
