# Q1697: middlewares/validate-authenticated-session — bot-gate confusion

## Question
Can an unprivileged attacker submit a bot user-agent that should be short-circuited to `setShopFromSessionOrToken` in `middlewares/validate-authenticated-session.ts` such that respondToBotRequest mis-handles a bot user-agent that should be short-circuited, altering the auth branch, breaking the invariant that bot handling cannot weaken auth, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` -> `setShopFromSessionOrToken`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a bot user-agent that should be short-circuited
- Exploit idea: respondToBotRequest mis-handles a bot user-agent that should be short-circuited, altering the auth branch
- Invariant to test: bot handling cannot weaken auth
- Expected Immunefi impact: Auth bypass (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: bot-UA branch test
