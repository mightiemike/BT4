# Q2944: helpers/validate-shop-and-host-params — redirect protocol/relative

## Question
Can an unprivileged attacker submit a redirect target that is protocol-relative (//evil.com) to `redirectToLoginPath` in `helpers/validate-shop-and-host-params.ts` such that sanitizeRedirectUrl/isSafe accepts a redirect target that is protocol-relative (//evil.com), breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `redirectToLoginPath`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirect target that is protocol-relative (//evil.com)
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a redirect target that is protocol-relative (//evil.com)
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
