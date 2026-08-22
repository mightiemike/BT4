# Q0032: utils/shop-admin-url-helper — open redirect via shop

## Question
Can an unprivileged attacker submit a shop param like 'evil.com?.myshopify.com' or with an embedded '@' to `shopAdminUrlToLegacyUrl` in `utils/shop-admin-url-helper.ts` such that shopAdminUrlToLegacyUrl treats a shop param like 'evil.com?.myshopify.com' or with an embedded '@' as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `shopAdminUrlToLegacyUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Exploit idea: shopAdminUrlToLegacyUrl treats a shop param like 'evil.com?.myshopify.com' or with an embedded '@' as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
