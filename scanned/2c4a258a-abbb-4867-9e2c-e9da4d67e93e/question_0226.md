# Q0226: utils/fetch-request — ssrf via shop/host

## Question
Can an unprivileged attacker submit a rawBody proxied to graphqlProxy without re-auth to `fetchRequest` in `utils/fetch-request.ts` such that fetchRequest builds the outbound API URL from a rawBody proxied to graphqlProxy without re-auth, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a rawBody proxied to graphqlProxy without re-auth
- Exploit idea: fetchRequest builds the outbound API URL from a rawBody proxied to graphqlProxy without re-auth
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
