# Q4614: clients/common — response trust

## Question
Can an unprivileged attacker submit a user-agent/host built from attacker-influenced config to `getUserAgent` in `clients/common.ts` such that serializeResponse/getUserAgent trusts a user-agent/host built from attacker-influenced config from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `getUserAgent`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a user-agent/host built from attacker-influenced config
- Exploit idea: serializeResponse/getUserAgent trusts a user-agent/host built from attacker-influenced config from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
