# Q4430: checkout/authenticate — installed-gate skip

## Question
Can an unprivileged attacker submit a document vs XHR request type mismatch to `authenticateCheckoutFactory` in `checkout/authenticate.ts` such that ensureInstalled/authenticateCheckoutFactory serves a document vs XHR request type mismatch without an installed session, breaking the invariant that installed check precedes app content, and leading to: access without install/consent?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/public/checkout/authenticate.ts` -> `authenticateCheckoutFactory`
- Entrypoint: Unauthenticated HTTP request to an authenticate.* handler
- Attacker controls: a document vs XHR request type mismatch
- Exploit idea: ensureInstalled/authenticateCheckoutFactory serves a document vs XHR request type mismatch without an installed session
- Invariant to test: installed check precedes app content
- Expected Immunefi impact: Access without install/consent (In scope: authentication/authorization bypass, cross-tenant access. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: no-session request test
