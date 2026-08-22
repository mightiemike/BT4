# Q1137: clients/common — token disclosure

## Question
Can an unprivileged attacker submit a redirect followed by http-fetch to an attacker location to `serializeResponse` in `clients/common.ts` such that serializeResponse places the access token where a redirect followed by http-fetch to an attacker location can read it, breaking the invariant that secrets never reach responses/logs/errors, and leading to: access-token leak?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `serializeResponse`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a redirect followed by http-fetch to an attacker location
- Exploit idea: serializeResponse places the access token where a redirect followed by http-fetch to an attacker location can read it
- Invariant to test: secrets never reach responses/logs/errors
- Expected Immunefi impact: Access-token leak (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: assert token absent from error/log
