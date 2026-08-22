# Q2430: helpers/validate-redirect-url — redirect protocol/relative

## Question
Can an unprivileged attacker submit a shop param like 'evil.com?.myshopify.com' or with an embedded '@' to `sanitizeRedirectUrl` in `helpers/validate-redirect-url.ts` such that sanitizeRedirectUrl/isSafe accepts a shop param like 'evil.com?.myshopify.com' or with an embedded '@', breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `sanitizeRedirectUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
