# Q4603: helpers/reject-bot-request — installed-gate skip

## Question
Can an unprivileged attacker submit a request replaying a stale session cookie to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that ensureInstalled/respondToBotRequest serves a request replaying a stale session cookie without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request replaying a stale session cookie
- Exploit idea: ensureInstalled/respondToBotRequest serves a request replaying a stale session cookie without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
