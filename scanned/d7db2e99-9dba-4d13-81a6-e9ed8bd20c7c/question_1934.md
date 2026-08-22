# Q1934: graphql_proxy/graphql_proxy — proxy without reauth

## Question
Can an unprivileged attacker submit a redirect followed by http-fetch to an attacker location to `graphqlProxy` in `graphql_proxy/graphql_proxy.ts` such that graphqlProxy forwards a redirect followed by http-fetch to an attacker location using session creds, breaking the invariant that proxied requests require caller auth, and leading to: confused-deputy admin api call?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/graphql_proxy/graphql_proxy.ts` -> `graphqlProxy`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a redirect followed by http-fetch to an attacker location
- Exploit idea: graphqlProxy forwards a redirect followed by http-fetch to an attacker location using session creds
- Invariant to test: proxied requests require caller auth
- Expected Immunefi impact: Confused-deputy Admin API call (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: unauth proxy test
