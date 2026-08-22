# Q3306: graphql-client/http-fetch — retry amplification

## Question
Can an unprivileged attacker submit a shop/host value causing the API request to target attacker host to `generateHttpFetch` in `graphql-client/http-fetch.ts` such that generateHttpFetch retries a shop/host value causing the API request to target attacker host amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts` -> `generateHttpFetch`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop/host value causing the API request to target attacker host
- Exploit idea: generateHttpFetch retries a shop/host value causing the API request to target attacker host amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
