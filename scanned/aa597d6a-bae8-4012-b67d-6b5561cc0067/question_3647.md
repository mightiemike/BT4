# Q3647: src/validations — retry amplification

## Question
Can an unprivileged attacker submit an api-version string selecting an unexpected endpoint to `validateRequiredStoreDomain` in `src/validations.ts` such that generateHttpFetch retries an api-version string selecting an unexpected endpoint amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredStoreDomain`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an api-version string selecting an unexpected endpoint
- Exploit idea: generateHttpFetch retries an api-version string selecting an unexpected endpoint amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
