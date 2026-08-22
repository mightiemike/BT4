# Q3191: src/validations — store-domain guard bypass

## Question
Can an unprivileged attacker submit an upstream redirect to a private-network address to `validateRequiredAccessToken` in `src/validations.ts` such that validateRequiredStoreDomain lets an upstream redirect to a private-network address proceed, breaking the invariant that store domain validated before use, and leading to: ssrf / request to attacker host?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredAccessToken`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an upstream redirect to a private-network address
- Exploit idea: validateRequiredStoreDomain lets an upstream redirect to a private-network address proceed
- Invariant to test: store domain validated before use
- Expected Immunefi impact: SSRF / request to attacker host (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: invalid-domain test
