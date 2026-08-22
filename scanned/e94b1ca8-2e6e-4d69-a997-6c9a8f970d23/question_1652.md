# Q1652: src/validations — proxy without reauth

## Question
Can an unprivileged attacker submit a session whose shop drives an outbound Admin API URL to `validateRequiredAccessToken` in `src/validations.ts` such that graphqlProxy forwards a session whose shop drives an outbound Admin API URL using session creds, breaking the invariant that proxied requests require caller auth, and leading to: confused-deputy admin api call?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredAccessToken`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a session whose shop drives an outbound Admin API URL
- Exploit idea: graphqlProxy forwards a session whose shop drives an outbound Admin API URL using session creds
- Invariant to test: proxied requests require caller auth
- Expected Immunefi impact: Confused-deputy Admin API call (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: unauth proxy test
