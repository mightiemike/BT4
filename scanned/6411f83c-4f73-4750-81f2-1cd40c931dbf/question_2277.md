# Q2277: clients/common — proxy without reauth

## Question
Can an unprivileged attacker submit a response deserialized from an attacker-influenced upstream to `serializeResponse` in `clients/common.ts` such that graphqlProxy forwards a response deserialized from an attacker-influenced upstream using session creds, breaking the invariant that proxied requests require caller auth, and leading to: confused-deputy admin api call?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `serializeResponse`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a response deserialized from an attacker-influenced upstream
- Exploit idea: graphqlProxy forwards a response deserialized from an attacker-influenced upstream using session creds
- Invariant to test: proxied requests require caller auth
- Expected Immunefi impact: Confused-deputy Admin API call (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: unauth proxy test
