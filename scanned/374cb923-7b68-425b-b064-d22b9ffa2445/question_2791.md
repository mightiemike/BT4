# Q2791: utils/fetch-request — store-domain guard bypass

## Question
Can an unprivileged attacker submit a retry loop amplifying requests to a chosen host to `fetchRequestFactory` in `utils/fetch-request.ts` such that validateRequiredStoreDomain lets a retry loop amplifying requests to a chosen host proceed, breaking the invariant that store domain validated before use, and leading to: ssrf / request to attacker host?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a retry loop amplifying requests to a chosen host
- Exploit idea: validateRequiredStoreDomain lets a retry loop amplifying requests to a chosen host proceed
- Invariant to test: store domain validated before use
- Expected Immunefi impact: SSRF / request to attacker host (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: invalid-domain test
