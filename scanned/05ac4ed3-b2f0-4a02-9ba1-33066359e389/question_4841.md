# Q4841: graphql_proxy/graphql_proxy — server-guard bypass

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that validateServerSideUsage bypassed via a session whose shop drives an outbound Admin API URL, breaking the invariant that server-only APIs unreachable from browser, and leading to: credential exposure?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: validateServerSideUsage bypassed via a session whose shop drives an outbound Admin API URL
- Invariant to test: server-only APIs unreachable from browser
- Expected Immunefi impact: Credential exposure (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: browser-context test
