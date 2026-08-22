# Q5240: graphql_proxy/graphql_proxy — server-guard bypass

## Question
Can an unprivileged attacker submit an api-version string selecting an unexpected endpoint to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that validateServerSideUsage bypassed via an api-version string selecting an unexpected endpoint, breaking the invariant that server-only APIs unreachable from browser, and leading to: credential exposure?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an api-version string selecting an unexpected endpoint
- Exploit idea: validateServerSideUsage bypassed via an api-version string selecting an unexpected endpoint
- Invariant to test: server-only APIs unreachable from browser
- Expected Immunefi impact: Credential exposure (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: browser-context test
