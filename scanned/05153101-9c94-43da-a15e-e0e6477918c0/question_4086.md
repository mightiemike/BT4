# Q4086: admin/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `getSessionTokenContext` in `admin/authenticate.ts` such that ensureInstalled/getSessionTokenContext serves a bot user-agent that should be short-circuited without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `getSessionTokenContext`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: ensureInstalled/getSessionTokenContext serves a bot user-agent that should be short-circuited without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
