# Q4769: helpers/get-shop-from-request — admin-url handle injection

## Question
Can an unprivileged attacker submit a host that decodes to a userinfo@ origin to `getShopFromRequest` in `helpers/get-shop-from-request.ts` such that shop-admin-url helpers mis-transform a host that decodes to a userinfo@ origin, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts` -> `getShopFromRequest`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that decodes to a userinfo@ origin
- Exploit idea: shop-admin-url helpers mis-transform a host that decodes to a userinfo@ origin
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
