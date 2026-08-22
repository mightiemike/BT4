# Q5468: graphql_proxy/graphql_proxy — server-guard bypass

## Question
Can an unprivileged attacker submit a response deserialized from an attacker-influenced upstream to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that validateServerSideUsage bypassed via a response deserialized from an attacker-influenced upstream, breaking the invariant that server-only APIs unreachable from browser, and leading to: credential exposure?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a response deserialized from an attacker-influenced upstream
- Exploit idea: validateServerSideUsage bypassed via a response deserialized from an attacker-influenced upstream
- Invariant to test: server-only APIs unreachable from browser
- Expected Immunefi impact: Credential exposure (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: browser-context test
