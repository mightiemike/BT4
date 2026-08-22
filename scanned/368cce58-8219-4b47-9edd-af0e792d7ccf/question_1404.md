# Q1404: helpers/validate-redirect-url — host allowlist bypass

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `sanitizeRedirectUrl` in `helpers/validate-redirect-url.ts` such that sanitizeRedirectUrl passes a host param with CRLF to split headers on redirect through the domain suffix check, breaking the invariant that host validated against exact allowed origins, and leading to: open redirect / embedded-url injection?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `sanitizeRedirectUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: sanitizeRedirectUrl passes a host param with CRLF to split headers on redirect through the domain suffix check
- Invariant to test: host validated against exact allowed origins
- Expected Immunefi impact: Open redirect / embedded-URL injection (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: suffix-bypass test
