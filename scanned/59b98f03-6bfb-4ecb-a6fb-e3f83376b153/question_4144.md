# Q4144: appProxy/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit an app-proxy request with a forged signature query to `authenticateAppProxyFactory` in `appProxy/authenticate.ts` such that ensureInstalled/authenticateAppProxyFactory serves an app-proxy request with a forged signature query without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `authenticateAppProxyFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy request with a forged signature query
- Exploit idea: ensureInstalled/authenticateAppProxyFactory serves an app-proxy request with a forged signature query without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
