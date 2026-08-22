# Q4364: utils/shop-admin-url-helper — admin-url handle injection

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `removeProtocol` in `utils/shop-admin-url-helper.ts` such that shop-admin-url helpers mis-transform a host that passes the '.myshopify.com$' suffix test via subdomain, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts` -> `removeProtocol`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: shop-admin-url helpers mis-transform a host that passes the '.myshopify.com$' suffix test via subdomain
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
