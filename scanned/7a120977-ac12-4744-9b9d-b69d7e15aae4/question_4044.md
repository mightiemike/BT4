# Q4044: clients/common — response trust

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `throwFailedRequest` in `clients/common.ts` such that serializeResponse/throwFailedRequest trusts a session whose shop drives an outbound Admin API URL from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `throwFailedRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: serializeResponse/throwFailedRequest trusts a session whose shop drives an outbound Admin API URL from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
