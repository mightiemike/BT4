# Q3361: utils/fetch-request — retry amplification

## Question
Can an unprivileged attacker submit an access token echoed into an error/log surface to `fetchRequestFactory` in `utils/fetch-request.ts` such that generateHttpFetch retries an access token echoed into an error/log surface amplifying requests, breaking the invariant that retries bounded and idempotent, and leading to: amplification/dos?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an access token echoed into an error/log surface
- Exploit idea: generateHttpFetch retries an access token echoed into an error/log surface amplifying requests
- Invariant to test: retries bounded and idempotent
- Expected Immunefi impact: Amplification/DoS (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: retry-count test
