# Q0045: middlewares/ensure-installed-on-shop — unauth route reach

## Question
Can an unprivileged attacker submit a request with no session token but a valid-looking shop param to `ensureInstalled` in `middlewares/ensure-installed-on-shop.ts` such that ensureInstalled exposes an authenticated context for a request with no session token but a valid-looking shop param without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `ensureInstalled`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request with no session token but a valid-looking shop param
- Exploit idea: ensureInstalled exposes an authenticated context for a request with no session token but a valid-looking shop param without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
