# Q2678: src/validations — store-domain guard bypass

## Question
Can an unprivileged attacker submit a store domain failing validateRequiredStoreDomain but still used to `validateRequiredAccessToken` in `src/validations.ts` such that validateRequiredStoreDomain lets a store domain failing validateRequiredStoreDomain but still used proceed, breaking the invariant that store domain validated before use, and leading to: ssrf / request to attacker host?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredAccessToken`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a store domain failing validateRequiredStoreDomain but still used
- Exploit idea: validateRequiredStoreDomain lets a store domain failing validateRequiredStoreDomain but still used proceed
- Invariant to test: store domain validated before use
- Expected Immunefi impact: SSRF / request to attacker host (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: invalid-domain test
