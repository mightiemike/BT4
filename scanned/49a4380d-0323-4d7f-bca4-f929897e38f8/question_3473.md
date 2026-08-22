# Q3473: graphql_proxy/graphql_proxy — retry amplification

## Question
Can an unprivileged attacker submit a store domain failing validateRequiredStoreDomain but still used to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that generateHttpFetch retries a store domain failing validateRequiredStoreDomain but still used amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a store domain failing validateRequiredStoreDomain but still used
- Exploit idea: generateHttpFetch retries a store domain failing validateRequiredStoreDomain but still used amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
