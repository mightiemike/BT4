# Q0158: middlewares/validate-authenticated-session — unauth route reach

## Question
Can an unprivileged attacker submit an app-proxy request with a forged signature query to `validateWithAuthCodeFlow` in `middlewares/validate-authenticated-session.ts` such that validateWithAuthCodeFlow exposes an authenticated context for an app-proxy request with a forged signature query without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithAuthCodeFlow`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy request with a forged signature query
- Exploit idea: validateWithAuthCodeFlow exposes an authenticated context for an app-proxy request with a forged signature query without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
