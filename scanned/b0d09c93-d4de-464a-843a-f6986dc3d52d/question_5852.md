# Q5852: helpers/reject-bot-request — options/cors leak

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that respondToOptionsRequest/CORS for a document vs XHR request type mismatch leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: respondToOptionsRequest/CORS for a document vs XHR request type mismatch leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
