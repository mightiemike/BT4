# Q1980: customer-account/authenticate — bot-gate confusion

## Question
Can an unprivileged attacker submit a request that skips the embedded/installed gate to `authenticateCustomerAccountFactory` in `customer-account/authenticate.ts` such that respondToBotRequest mis-handles a request that skips the embedded/installed gate, altering the auth branch, breaking the invariant that bot handling cannot weaken auth, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/customer-account/authenticate.ts` -> `authenticateCustomerAccountFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a request that skips the embedded/installed gate
- Exploit idea: respondToBotRequest mis-handles a request that skips the embedded/installed gate, altering the auth branch
- Invariant to test: bot handling cannot weaken auth
- Expected Immunefi impact: Auth bypass (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: bot-UA branch test
