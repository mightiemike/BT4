# Q3565: utils/shop-validator — regex backtracking DoS

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `sanitizeShop` in `utils/shop-validator.ts` such that the shop/host RegExp in sanitizeShop backtracks catastrophically on a host that passes the '.myshopify.com$' suffix test via subdomain, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeShop`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: the shop/host RegExp in sanitizeShop backtracks catastrophically on a host that passes the '.myshopify.com$' suffix test via subdomain
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
