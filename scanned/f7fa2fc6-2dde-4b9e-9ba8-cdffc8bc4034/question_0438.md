# Q0438: admin/authenticate — unauth route reach

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `getSessionTokenContext` in `admin/authenticate.ts` such that getSessionTokenContext exposes an authenticated context for a document vs XHR request type mismatch without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `getSessionTokenContext`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: getSessionTokenContext exposes an authenticated context for a document vs XHR request type mismatch without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
