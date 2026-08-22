# Q4548: middlewares/ensure-installed-on-shop — installed-gate skip

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `deleteAppInstallationHandler` in `middlewares/ensure-installed-on-shop.ts` such that ensureInstalled/deleteAppInstallationHandler serves an authenticated route reached before session validation runs without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `deleteAppInstallationHandler`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: ensureInstalled/deleteAppInstallationHandler serves an authenticated route reached before session validation runs without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
