# Q4035: middlewares/ensure-installed-on-shop — installed-gate skip

## Question
Can an unprivileged attacker submit a request with no session token but a valid-looking shop param to `sessionHasValidAccessToken` in `middlewares/ensure-installed-on-shop.ts` such that ensureInstalled/sessionHasValidAccessToken serves a request with no session token but a valid-looking shop param without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `sessionHasValidAccessToken`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request with no session token but a valid-looking shop param
- Exploit idea: ensureInstalled/sessionHasValidAccessToken serves a request with no session token but a valid-looking shop param without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
