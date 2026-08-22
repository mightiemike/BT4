# Q3394: utils/shop-validator — regex backtracking DoS

## Question
Can an unprivileged attacker submit a redirectUrl with '\\/\\/evil.com' or backslash tricks to `sanitizeHost` in `utils/shop-validator.ts` such that the shop/host RegExp in sanitizeHost backtracks catastrophically on a redirectUrl with '\\/\\/evil.com' or backslash tricks, breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl with '\\/\\/evil.com' or backslash tricks
- Exploit idea: the shop/host RegExp in sanitizeHost backtracks catastrophically on a redirectUrl with '\\/\\/evil.com' or backslash tricks
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
