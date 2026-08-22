# Q3864: middlewares/ensure-installed-on-shop — customer/checkout token reuse

## Question
Can an unprivileged attacker submit a crafted Accept/Sec-Fetch header steering the auth branch to `deleteAppInstallationHandler` in `middlewares/ensure-installed-on-shop.ts` such that deleteAppInstallationHandler accepts a crafted Accept/Sec-Fetch header steering the auth branch from a different session/customer, breaking the invariant that public tokens bound to their session, and leading to: cross-user access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `deleteAppInstallationHandler`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a crafted Accept/Sec-Fetch header steering the auth branch
- Exploit idea: deleteAppInstallationHandler accepts a crafted Accept/Sec-Fetch header steering the auth branch from a different session/customer
- Invariant to test: public tokens bound to their session
- Expected Immunefi impact: Cross-user access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: token-reuse test
