# Q1421: graphql_proxy/graphql_proxy — token disclosure

## Question
Can an unprivileged attacker submit a user-agent/host built from attacker-influenced config to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that graphqlProxy places the access token where a user-agent/host built from attacker-influenced config can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a user-agent/host built from attacker-influenced config
- Exploit idea: graphqlProxy places the access token where a user-agent/host built from attacker-influenced config can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
