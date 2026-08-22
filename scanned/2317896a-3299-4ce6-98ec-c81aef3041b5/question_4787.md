# Q4787: src/validations — response trust

## Question
Can an unprivileged attacker submit an upstream redirect to a private-network address to `validateServerSideUsage` in `src/validations.ts` such that serializeResponse/validateServerSideUsage trusts an upstream redirect to a private-network address from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateServerSideUsage`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: an upstream redirect to a private-network address
- Exploit idea: serializeResponse/validateServerSideUsage trusts an upstream redirect to a private-network address from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
