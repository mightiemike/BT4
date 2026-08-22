# Q1806: admin/authenticate — bot-gate confusion

## Question
Can an unprivileged attacker submit an OPTIONS/CORS preflight abused to leak headers to `getSessionTokenContext` in `admin/authenticate.ts` such that respondToBotRequest mis-handles an OPTIONS/CORS preflight abused to leak headers, altering the auth branch, breaking the invariant that bot handling cannot weaken auth, and leading to: auth bypass?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `getSessionTokenContext`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an OPTIONS/CORS preflight abused to leak headers
- Exploit idea: respondToBotRequest mis-handles an OPTIONS/CORS preflight abused to leak headers, altering the auth branch
- Invariant to test: bot handling cannot weaken auth
- Expected Immunefi impact: Auth bypass (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: bot-UA branch test
