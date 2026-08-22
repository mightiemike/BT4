# Q1742: utils/shop-admin-url-helper — decodeHost origin injection

## Question
Can an unprivileged attacker submit a host param base64-decoding to an attacker origin to `spinAdminUrlToLegacyUrl` in `utils/shop-admin-url-helper.ts` such that decodeHost yields an attacker origin from a host param base64-decoding to an attacker origin, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `spinAdminUrlToLegacyUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param base64-decoding to an attacker origin
- Exploit idea: decodeHost yields an attacker origin from a host param base64-decoding to an attacker origin
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
