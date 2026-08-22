# Q4205: middlewares/validate-authenticated-session — installed-gate skip

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `handleSessionError` in `middlewares/validate-authenticated-session.ts` such that ensureInstalled/handleSessionError serves an OPTIONS/CORS preflight abused to leak headers without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `handleSessionError`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: ensureInstalled/handleSessionError serves an OPTIONS/CORS preflight abused to leak headers without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
