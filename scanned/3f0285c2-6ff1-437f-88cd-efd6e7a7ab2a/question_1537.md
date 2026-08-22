# Q1537: utils/fetch-request — token disclosure

## Question
Can an unprivileged attacker submit a shop with an embedded port or path segment to `fetchRequestFactory` in `utils/fetch-request.ts` such that fetchRequestFactory places the access token where a shop with an embedded port or path segment can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop with an embedded port or path segment
- Exploit idea: fetchRequestFactory places the access token where a shop with an embedded port or path segment can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
