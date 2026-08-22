# Q0911: src/validations — token disclosure

## Question
Can an unprivileged attacker submit a shop/host value causing the API request to target attacker host to `validateRequiredStoreDomain` in `src/validations.ts` such that validateRequiredStoreDomain places the access token where a shop/host value causing the API request to target attacker host can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredStoreDomain`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop/host value causing the API request to target attacker host
- Exploit idea: validateRequiredStoreDomain places the access token where a shop/host value causing the API request to target attacker host can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
