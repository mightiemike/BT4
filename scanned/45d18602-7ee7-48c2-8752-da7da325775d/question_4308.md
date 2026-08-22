# Q4308: utils/domain-transformer — admin-url handle injection

## Question
Can an unprivileged attacker submit a shop with a trailing dot, path, or extra label to `getTransformationDomains` in `utils/domain-transformer.ts` such that shop-admin-url helpers mis-transform a shop with a trailing dot, path, or extra label, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `getTransformationDomains`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop with a trailing dot, path, or extra label
- Exploit idea: shop-admin-url helpers mis-transform a shop with a trailing dot, path, or extra label
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
