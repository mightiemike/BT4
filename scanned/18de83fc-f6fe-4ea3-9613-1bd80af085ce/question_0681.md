# Q0681: clients/common — ssrf via shop/host

## Question
Can an unprivileged attacker submit a response deserialized from an attacker-influenced upstream to `serializeResponse` in `clients/common.ts` such that serializeResponse builds the outbound API URL from a response deserialized from an attacker-influenced upstream, breaking the invariant that API host restricted to the verified shop's domain, and leading to: ssrf with app credentials?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `serializeResponse`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a response deserialized from an attacker-influenced upstream
- Exploit idea: serializeResponse builds the outbound API URL from a response deserialized from an attacker-influenced upstream
- Invariant to test: API host restricted to the verified shop's domain
- Expected Immunefi impact: SSRF with app credentials (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection URL test
