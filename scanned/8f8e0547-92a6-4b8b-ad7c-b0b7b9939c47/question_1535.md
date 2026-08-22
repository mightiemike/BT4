# Q1535: graphql_proxy/graphql_proxy — token disclosure

## Question
Can an unprivileged attacker submit a shop with an embedded port or path segment to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that graphqlProxy places the access token where a shop with an embedded port or path segment can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop with an embedded port or path segment
- Exploit idea: graphqlProxy places the access token where a shop with an embedded port or path segment can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
