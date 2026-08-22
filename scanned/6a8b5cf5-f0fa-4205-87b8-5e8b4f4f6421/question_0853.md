# Q0853: utils/fetch-request — token disclosure

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `fetchRequestFactory` in `utils/fetch-request.ts` such that fetchRequestFactory places the access token where a session whose shop drives an outbound Admin API URL can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: fetchRequestFactory places the access token where a session whose shop drives an outbound Admin API URL can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
