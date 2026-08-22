# Q0855: graphql-client/http-fetch — token disclosure

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `generateHttpFetch` in `graphql-client/http-fetch.ts` such that generateHttpFetch places the access token where a session whose shop drives an outbound Admin API URL can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts` -> `generateHttpFetch`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: generateHttpFetch places the access token where a session whose shop drives an outbound Admin API URL can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
