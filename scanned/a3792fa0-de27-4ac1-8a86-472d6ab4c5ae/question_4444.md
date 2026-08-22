# Q4444: utils/fetch-request — response trust

## Question
Can an unprivileged attacker submit an api-version string selecting an unexpected endpoint to `fetchRequest` in `utils/fetch-request.ts` such that serializeResponse/fetchRequest trusts an api-version string selecting an unexpected endpoint from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an api-version string selecting an unexpected endpoint
- Exploit idea: serializeResponse/fetchRequest trusts an api-version string selecting an unexpected endpoint from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
