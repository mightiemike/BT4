# Q2964: graphql-client/http-fetch — store-domain guard bypass

## Question
Can an unprivileged attacker submit a server-side usage guard bypassed from a browser context to `generateHttpFetch` in `graphql-client/http-fetch.ts` such that validateRequiredStoreDomain lets a server-side usage guard bypassed from a browser context proceed, breaking the invariant that store domain validated before use, and leading to: ssrf / request to attacker host?

## Target
- File/function: `packages/api-clients/graphql-client/src/graphql-client/http-fetch.ts` -> `generateHttpFetch`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a server-side usage guard bypassed from a browser context
- Exploit idea: validateRequiredStoreDomain lets a server-side usage guard bypassed from a browser context proceed
- Invariant to test: store domain validated before use
- Expected Immunefi impact: SSRF / request to attacker host (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: invalid-domain test
