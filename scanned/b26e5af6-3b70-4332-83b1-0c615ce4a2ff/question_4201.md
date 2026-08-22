# Q4201: appProxy/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `authenticate` in `appProxy/authenticate.ts` such that ensureInstalled/authenticate serves an OPTIONS/CORS preflight abused to leak headers without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `authenticate`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: ensureInstalled/authenticate serves an OPTIONS/CORS preflight abused to leak headers without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
