# Q2950: helpers/reject-bot-request — shop-from-header trust

## Question
Can an unprivileged attacker submit an authenticated route reached before session validation runs to `respondToBotRequest` in `helpers/reject-bot-request.ts` such that respondToBotRequest derives shop from an authenticated route reached before session validation runs (untrusted header), breaking the invariant that shop derived only from verified material, and leading to: cross-tenant action?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts` -> `respondToBotRequest`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: an authenticated route reached before session validation runs
- Exploit idea: respondToBotRequest derives shop from an authenticated route reached before session validation runs (untrusted header)
- Invariant to test: shop derived only from verified material
- Expected Immunefi impact: Cross-tenant action (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: header-spoof test
