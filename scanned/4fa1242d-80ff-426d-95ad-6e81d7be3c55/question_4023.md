# Q4023: utils/domain-transformer — admin-url handle injection

## Question
Can an unprivileged attacker submit a shop param like 'evil.com?.myshopify.com' or with an embedded '@' to `applyDomainTransformations` in `utils/domain-transformer.ts` such that shop-admin-url helpers mis-transform a shop param like 'evil.com?.myshopify.com' or with an embedded '@', breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `applyDomainTransformations`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Exploit idea: shop-admin-url helpers mis-transform a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
