# Q3587: graphql_proxy/graphql_proxy — retry amplification

## Question
Can an unprivileged attacker submit a retry loop amplifying requests to a chosen host to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that generateHttpFetch retries a retry loop amplifying requests to a chosen host amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a retry loop amplifying requests to a chosen host
- Exploit idea: generateHttpFetch retries a retry loop amplifying requests to a chosen host amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
