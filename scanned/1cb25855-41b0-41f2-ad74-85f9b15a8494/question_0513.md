# Q0513: graphql-client/http-fetch — ssrf via shop/host

## Question
Can an unprivileged attacker submit a header set from untrusted session fields to `generateHttpFetch` in `graphql-client/http-fetch.ts` such that generateHttpFetch builds the outbound API URL from a header set from untrusted session fields, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts` -> `generateHttpFetch`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a header set from untrusted session fields
- Exploit idea: generateHttpFetch builds the outbound API URL from a header set from untrusted session fields
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
