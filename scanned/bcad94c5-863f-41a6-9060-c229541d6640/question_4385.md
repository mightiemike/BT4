# Q4385: graphql_proxy/graphql_proxy — response trust

## Question
Can an unprivileged attacker submit a retry loop amplifying requests to a chosen host to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that serializeResponse/graphqlProxy trusts a retry loop amplifying requests to a chosen host from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a retry loop amplifying requests to a chosen host
- Exploit idea: serializeResponse/graphqlProxy trusts a retry loop amplifying requests to a chosen host from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
