# Q4489: helpers/reject-bot-request — installed-gate skip

## Question
Can an unprivileged attacker submit an app-proxy logged-in-customer id claim under attacker control to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that ensureInstalled/respondToBotRequest serves an app-proxy logged-in-customer id claim under attacker control without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy logged-in-customer id claim under attacker control
- Exploit idea: ensureInstalled/respondToBotRequest serves an app-proxy logged-in-customer id claim under attacker control without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
