# Q1924: helpers/reject-bot-request — bot-gate confusion

## Question
Can an unprivileged attacker submit a customer-account/checkout token from a different session to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that respondToBotRequest mis-handles a customer-account/checkout token from a different session, altering the auth branch, breaking the invariant that bot handling cannot weaken auth, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a customer-account/checkout token from a different session
- Exploit idea: respondToBotRequest mis-handles a customer-account/checkout token from a different session, altering the auth branch
- Invariant to test: bot handling cannot weaken auth
- Expected Immunefi impact: Auth bypass (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: bot-UA branch test
