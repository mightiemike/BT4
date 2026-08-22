# Q4648: utils/shop-validator — admin-url handle injection

## Question
Can an unprivileged attacker submit a shop string long enough to trigger regex backtracking to `sanitizeHost` in `utils/shop-validator.ts` such that shop-admin-url helpers mis-transform a shop string long enough to trigger regex backtracking, breaking the invariant that store handle mapping is 1:1 and sanitized, and leading to: redirect/host confusion?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop string long enough to trigger regex backtracking
- Exploit idea: shop-admin-url helpers mis-transform a shop string long enough to trigger regex backtracking
- Invariant to test: store handle mapping is 1:1 and sanitized
- Expected Immunefi impact: Redirect/host confusion (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: crafted store handle test
