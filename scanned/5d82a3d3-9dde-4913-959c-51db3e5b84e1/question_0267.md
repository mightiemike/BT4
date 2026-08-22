# Q0267: admin/authenticate — unauth route reach

## Question
Can an unprivileged attacker submit a request whose shop is derived from an untrusted header to `addEmbeddedFeatures` in `admin/authenticate.ts` such that addEmbeddedFeatures exposes an authenticated context for a request whose shop is derived from an untrusted header without a valid session token, breaking the invariant that no protected work before token verification, and leading to: auth bypass / unauthenticated access?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `addEmbeddedFeatures`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request whose shop is derived from an untrusted header
- Exploit idea: addEmbeddedFeatures exposes an authenticated context for a request whose shop is derived from an untrusted header without a valid session token
- Invariant to test: no protected work before token verification
- Expected Immunefi impact: Auth bypass / unauthenticated access (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: request without token, assert 401/redirect
