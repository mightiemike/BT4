# Q4557: clients/common — response trust

## Question
Can an unprivileged attacker submit a server-side usage guard bypassed from a browser context to `serializeResponse` in `clients/common.ts` such that serializeResponse/serializeResponse trusts a server-side usage guard bypassed from a browser context from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/apps/shopify-api/lib/clients/common.ts` -> `serializeResponse`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a server-side usage guard bypassed from a browser context
- Exploit idea: serializeResponse/serializeResponse trusts a server-side usage guard bypassed from a browser context from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
