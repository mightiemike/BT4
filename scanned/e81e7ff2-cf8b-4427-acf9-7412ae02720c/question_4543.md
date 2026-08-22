# Q4543: appProxy/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `processLiquidBody` in `appProxy/authenticate.ts` such that ensureInstalled/processLiquidBody serves an authenticated route reached before session validation runs without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/appProxy/authenticate.ts` -> `processLiquidBody`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: ensureInstalled/processLiquidBody serves an authenticated route reached before session validation runs without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
