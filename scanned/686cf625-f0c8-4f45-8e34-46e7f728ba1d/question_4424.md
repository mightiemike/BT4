# Q4424: auth/get-embedded-app-url — admin-url handle injection

## Question
Can an unprivileged attacker submit a shop admin URL (admin.shopify.com/store/x) with crafted store handle to `buildEmbeddedAppUrl` in `auth/get-embedded-app-url.ts` such that shop-admin-url helpers mis-transform a shop admin URL (admin.shopify.com/store/x) with crafted store handle, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts` -> `buildEmbeddedAppUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Exploit idea: shop-admin-url helpers mis-transform a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
