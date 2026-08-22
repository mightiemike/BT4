# Q5882: admin/authenticate — options/cors leak

## Question
Can an unprivileged attacker submit an app-proxy logged-in-customer id claim under attacker control to `respondToExitIframeRequest` in `admin/authenticate.ts` such that respondToOptionsRequest/CORS for an app-proxy logged-in-customer id claim under attacker control leaks headers/state, breaking the invariant that preflight is side-effect free, and leading to: info disclosure?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts` -> `respondToExitIframeRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an app-proxy logged-in-customer id claim under attacker control
- Exploit idea: respondToOptionsRequest/CORS for an app-proxy logged-in-customer id claim under attacker control leaks headers/state
- Invariant to test: preflight is side-effect free
- Expected Immunefi impact: Info disclosure (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: OPTIONS test
