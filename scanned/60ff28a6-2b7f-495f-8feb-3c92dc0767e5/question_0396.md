# Q0396: clients/common — ssrf via shop/host

## Question
Can an unprivileged attacker submit a retry loop amplifying requests to a chosen host to `throwFailedRequest` in `clients/common.ts` such that throwFailedRequest builds the outbound API URL from a retry loop amplifying requests to a chosen host, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `throwFailedRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a retry loop amplifying requests to a chosen host
- Exploit idea: throwFailedRequest builds the outbound API URL from a retry loop amplifying requests to a chosen host
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
