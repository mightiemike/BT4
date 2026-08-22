# Q3420: graphql-client/http-fetch — retry amplification

## Question
Can an unprivileged attacker submit a rawBody proxied to graphqlProxy without re-auth to `generateHttpFetch` in `graphql-client/http-fetch.ts` such that generateHttpFetch retries a rawBody proxied to graphqlProxy without re-auth amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts` -> `generateHttpFetch`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a rawBody proxied to graphqlProxy without re-auth
- Exploit idea: generateHttpFetch retries a rawBody proxied to graphqlProxy without re-auth amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
