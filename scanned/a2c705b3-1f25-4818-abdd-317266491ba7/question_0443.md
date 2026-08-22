# Q0443: middlewares/validate-authenticated-session — unauth route reach

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `validateWithAuthCodeFlow` in `middlewares/validate-authenticated-session.ts` such that validateWithAuthCodeFlow exposes an authenticated context for a document vs XHR request type mismatch without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithAuthCodeFlow`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: validateWithAuthCodeFlow exposes an authenticated context for a document vs XHR request type mismatch without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
