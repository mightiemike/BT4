# Q3057: helpers/validate-redirect-url — redirect protocol/relative

## Question
Can an unprivileged attacker submit a shop string long enough to trigger regex backtracking to `isSafe` in `helpers/validate-redirect-url.ts` such that sanitizeRedirectUrl/isSafe accepts a shop string long enough to trigger regex backtracking, breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `isSafe`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop string long enough to trigger regex backtracking
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a shop string long enough to trigger regex backtracking
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
