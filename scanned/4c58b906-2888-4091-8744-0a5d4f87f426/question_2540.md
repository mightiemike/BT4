# Q2540: utils/shop-admin-url-helper — redirect protocol/relative

## Question
Can an unprivileged attacker submit a host param base64-decoding to an attacker origin to `spinAdminUrlToLegacyUrl` in `utils/shop-admin-url-helper.ts` such that sanitizeRedirectUrl/isSafe accepts a host param base64-decoding to an attacker origin, breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `spinAdminUrlToLegacyUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param base64-decoding to an attacker origin
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a host param base64-decoding to an attacker origin
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
