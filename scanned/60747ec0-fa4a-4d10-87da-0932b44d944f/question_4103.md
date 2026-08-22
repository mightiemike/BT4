# Q4103: src/validations — response trust

## Question
Can an unprivileged attacker submit a shop/host value causing the API request to target attacker host to `validateServerSideUsage` in `src/validations.ts` such that serializeResponse/validateServerSideUsage trusts a shop/host value causing the API request to target attacker host from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateServerSideUsage`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a shop/host value causing the API request to target attacker host
- Exploit idea: serializeResponse/validateServerSideUsage trusts a shop/host value causing the API request to target attacker host from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
