# Q0668: checkout/authenticate — unauth route reach

## Question
Can an unprivileged attacker submit a crafted Accept/Sec-Fetch header steering the auth branch to `authenticateCheckoutFactory` in `checkout/authenticate.ts` such that authenticateCheckoutFactory exposes an authenticated context for a crafted Accept/Sec-Fetch header steering the auth branch without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/checkout/authenticate.ts` -> `authenticateCheckoutFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a crafted Accept/Sec-Fetch header steering the auth branch
- Exploit idea: authenticateCheckoutFactory exposes an authenticated context for a crafted Accept/Sec-Fetch header steering the auth branch without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
