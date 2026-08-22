# Q0281: graphql_proxy/graphql_proxy — ssrf via shop/host

## Question
Can an unprivileged attacker submit a store domain failing validateRequiredStoreDomain but still used to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that graphqlProxy builds the outbound API URL from a store domain failing validateRequiredStoreDomain but still used, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a store domain failing validateRequiredStoreDomain but still used
- Exploit idea: graphqlProxy builds the outbound API URL from a store domain failing validateRequiredStoreDomain but still used
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
