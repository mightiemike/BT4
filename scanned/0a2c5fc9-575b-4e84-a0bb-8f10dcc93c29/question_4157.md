# Q4157: graphql_proxy/graphql_proxy — response trust

## Question
Can an unprivileged attacker submit an access token echoed into an error/log surface to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that serializeResponse/graphqlProxy trusts an access token echoed into an error/log surface from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an access token echoed into an error/log surface
- Exploit idea: serializeResponse/graphqlProxy trusts an access token echoed into an error/log surface from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
