# Q3000: helpers/validate-redirect-url — redirect protocol/relative

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `sanitizeRedirectUrl` in `helpers/validate-redirect-url.ts` such that sanitizeRedirectUrl/isSafe accepts a host param with CRLF to split headers on redirect, breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `sanitizeRedirectUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a host param with CRLF to split headers on redirect
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
