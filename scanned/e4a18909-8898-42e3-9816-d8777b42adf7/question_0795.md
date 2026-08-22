# Q0795: clients/common — ssrf via shop/host

## Question
Can an unprivileged attacker submit an upstream redirect to a private-network address to `clientLoggerFactory` in `clients/common.ts` such that clientLoggerFactory builds the outbound API URL from an upstream redirect to a private-network address, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `clientLoggerFactory`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an upstream redirect to a private-network address
- Exploit idea: clientLoggerFactory builds the outbound API URL from an upstream redirect to a private-network address
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
