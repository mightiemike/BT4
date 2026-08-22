# Q5984: admin/authenticate — options/cors leak

## Question
Can an unprivileged attacker submit a crafted Accept/Sec-Fetch header steering the auth branch to `addScopesFeatures` in `admin/authenticate.ts` such that respondToOptionsRequest/CORS for a crafted Accept/Sec-Fetch header steering the auth branch leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `addScopesFeatures`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a crafted Accept/Sec-Fetch header steering the auth branch
- Exploit idea: respondToOptionsRequest/CORS for a crafted Accept/Sec-Fetch header steering the auth branch leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
