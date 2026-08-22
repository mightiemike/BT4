# Q4592: utils/shop-admin-url-helper — admin-url handle injection

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `localAdminUrlToLegacyUrl` in `utils/shop-admin-url-helper.ts` such that shop-admin-url helpers mis-transform a host param with CRLF to split headers on redirect, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `localAdminUrlToLegacyUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: shop-admin-url helpers mis-transform a host param with CRLF to split headers on redirect
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
