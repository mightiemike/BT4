# Q3532: utils/fetch-request — retry amplification

## Question
Can an unprivileged attacker submit a redirect followed by http-fetch to an attacker location to `fetchRequest` in `utils/fetch-request.ts` such that generateHttpFetch retries a redirect followed by http-fetch to an attacker location amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a redirect followed by http-fetch to an attacker location
- Exploit idea: generateHttpFetch retries a redirect followed by http-fetch to an attacker location amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
