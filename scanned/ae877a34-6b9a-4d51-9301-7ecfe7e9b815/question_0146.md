# Q0146: utils/shop-admin-url-helper — open redirect via shop

## Question
Can an unprivileged attacker submit a host param base64-decoding to an attacker origin to `spinAdminUrlToLegacyUrl` in `utils/shop-admin-url-helper.ts` such that spinAdminUrlToLegacyUrl treats a host param base64-decoding to an attacker origin as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `spinAdminUrlToLegacyUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param base64-decoding to an attacker origin
- Exploit idea: spinAdminUrlToLegacyUrl treats a host param base64-decoding to an attacker origin as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a host param base64-decoding to an attacker origin
