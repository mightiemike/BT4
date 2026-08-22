# Q1755: middlewares/ensure-installed-on-shop — bot-gate confusion

## Question
Can an unprivileged attacker submit an app-proxy request with a forged signature query to `ensureInstalled` in `middlewares/ensure-installed-on-shop.ts` such that respondToBotRequest mis-handles an app-proxy request with a forged signature query, altering the auth branch, breaking the invariant that bot handling cannot weaken auth, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` -> `ensureInstalled`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy request with a forged signature query
- Exploit idea: respondToBotRequest mis-handles an app-proxy request with a forged signature query, altering the auth branch
- Invariant to test: bot handling cannot weaken auth
- Expected Immunefi impact: Auth bypass (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: bot-UA branch test
