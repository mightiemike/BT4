# Q1082: src/validations — token disclosure

## Question
Can an unprivileged attacker submit a store domain failing validateRequiredStoreDomain but still used to `validateRequiredStoreDomain` in `src/validations.ts` such that validateRequiredStoreDomain places the access token where a store domain failing validateRequiredStoreDomain but still used can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredStoreDomain`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a store domain failing validateRequiredStoreDomain but still used
- Exploit idea: validateRequiredStoreDomain places the access token where a store domain failing validateRequiredStoreDomain but still used can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
