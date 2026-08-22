# Q0112: utils/fetch-request — ssrf via shop/host

## Question
Can an unprivileged attacker submit a shop/host value causing the API request to target attacker host to `fetchRequest` in `utils/fetch-request.ts` such that fetchRequest builds the outbound API URL from a shop/host value causing the API request to target attacker host, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/fetch-request.ts` -> `fetchRequest`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop/host value causing the API request to target attacker host
- Exploit idea: fetchRequest builds the outbound API URL from a shop/host value causing the API request to target attacker host
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
