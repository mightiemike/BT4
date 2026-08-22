# Q3417: clients/common — retry amplification

## Question
Can an unprivileged attacker submit a rawBody proxied to graphqlProxy without re-auth to `serializeResponse` in `clients/common.ts` such that generateHttpFetch retries a rawBody proxied to graphqlProxy without re-auth amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `serializeResponse`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a rawBody proxied to graphqlProxy without re-auth
- Exploit idea: generateHttpFetch retries a rawBody proxied to graphqlProxy without re-auth amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
