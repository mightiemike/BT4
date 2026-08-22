# Q4331: src/validations — response trust

## Question
Can an unprivileged attacker submit a redirect followed by http-fetch to an attacker location to `validateRequiredStoreDomain` in `src/validations.ts` such that serializeResponse/validateRequiredStoreDomain trusts a redirect followed by http-fetch to an attacker location from upstream, breaking the invariant that upstream response validated before use, and leading to: injection via response?

## Target
- File/function: `packages/api-clients/admin-api-client/src/validations.ts` -> `validateRequiredStoreDomain`
- Entrypoint: Request that triggers an outbound Admin/Storefront API call
- Attacker controls: a redirect followed by http-fetch to an attacker location
- Exploit idea: serializeResponse/validateRequiredStoreDomain trusts a redirect followed by http-fetch to an attacker location from upstream
- Invariant to test: upstream response validated before use
- Expected Immunefi impact: Injection via response (In scope: SSRF with app credentials or access-token disclosure. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: malformed-response test
