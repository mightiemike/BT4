# Q2881: utils/shop-validator — redirect protocol/relative

## Question
Can an unprivileged attacker submit a custom shop domain injected via the transformation-domains config to `sanitizeShop` in `utils/shop-validator.ts` such that sanitizeRedirectUrl/isSafe accepts a custom shop domain injected via the transformation-domains config, breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeShop`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a custom shop domain injected via the transformation-domains config
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a custom shop domain injected via the transformation-domains config
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
