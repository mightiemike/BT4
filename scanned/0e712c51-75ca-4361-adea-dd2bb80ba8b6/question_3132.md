# Q3132: clients/common — store-domain guard bypass

## Question
Can an unprivileged attacker submit a shop with an embedded port or path segment to `throwFailedRequest` in `clients/common.ts` such that validateRequiredStoreDomain lets a shop with an embedded port or path segment proceed, breaking the invariant that store domain validated before use, and leading to: ssrf / request to attacker host?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `throwFailedRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop with an embedded port or path segment
- Exploit idea: validateRequiredStoreDomain lets a shop with an embedded port or path segment proceed
- Invariant to test: store domain validated before use
- Expected Immunefi impact: SSRF / request to attacker host (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: invalid-domain test
