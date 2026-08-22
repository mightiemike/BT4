# Q0610: appProxy/authenticate — unauth route reach

## Question
Can an unprivileged attacker submit a request replaying a stale session cookie to `validateAppProxyHmac` in `appProxy/authenticate.ts` such that validateAppProxyHmac exposes an authenticated context for a request replaying a stale session cookie without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `validateAppProxyHmac`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request replaying a stale session cookie
- Exploit idea: validateAppProxyHmac exposes an authenticated context for a request replaying a stale session cookie without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
