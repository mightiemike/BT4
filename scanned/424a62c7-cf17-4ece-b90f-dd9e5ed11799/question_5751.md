# Q5751: middlewares/validate-authenticated-session — options/cors leak

## Question
Can an unprivileged attacker submit a request whose shop is derived from an untrusted header to `validateWithAuthCodeFlow` in `middlewares/validate-authenticated-session.ts` such that respondToOptionsRequest/CORS for a request whose shop is derived from an untrusted header leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `validateWithAuthCodeFlow`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request whose shop is derived from an untrusted header
- Exploit idea: respondToOptionsRequest/CORS for a request whose shop is derived from an untrusted header leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
