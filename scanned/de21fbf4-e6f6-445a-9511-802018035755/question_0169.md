# Q0169: utils/fetch-request — ssrf via shop/host

## Question
Can an unprivileged attacker submit an access token echoed into an error/log surface to `fetchRequestFactory` in `utils/fetch-request.ts` such that fetchRequestFactory builds the outbound API URL from an access token echoed into an error/log surface, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequestFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an access token echoed into an error/log surface
- Exploit idea: fetchRequestFactory builds the outbound API URL from an access token echoed into an error/log surface
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
