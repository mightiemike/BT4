# Q3817: utils/fetch-request — retry amplification

## Question
Can an unprivileged attacker submit a user-agent/host built from attacker-influenced config to `fetchRequestFactory` in `utils/fetch-request.ts` such that generateHttpFetch retries a user-agent/host built from attacker-influenced config amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a user-agent/host built from attacker-influenced config
- Exploit idea: generateHttpFetch retries a user-agent/host built from attacker-influenced config amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
