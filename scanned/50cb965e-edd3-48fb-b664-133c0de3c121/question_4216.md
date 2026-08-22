# Q4216: utils/fetch-request — response trust

## Question
Can an unprivileged attacker submit a rawBody proxied to graphqlProxy without re-auth to `fetchRequest` in `utils/fetch-request.ts` such that serializeResponse/fetchRequest trusts a rawBody proxied to graphqlProxy without re-auth from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a rawBody proxied to graphqlProxy without re-auth
- Exploit idea: serializeResponse/fetchRequest trusts a rawBody proxied to graphqlProxy without re-auth from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
