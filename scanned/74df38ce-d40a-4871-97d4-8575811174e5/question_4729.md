# Q4729: utils/fetch-request — response trust

## Question
Can an unprivileged attacker submit a shop with an embedded port or path segment to `fetchRequestFactory` in `utils/fetch-request.ts` such that serializeResponse/fetchRequestFactory trusts a shop with an embedded port or path segment from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop with an embedded port or path segment
- Exploit idea: serializeResponse/fetchRequestFactory trusts a shop with an embedded port or path segment from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
