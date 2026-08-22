# Q3736: utils/shop-validator — regex backtracking DoS

## Question
Can an unprivileged attacker submit a redirect target that is protocol-relative (//evil.com) to `sanitizeHost` in `utils/shop-validator.ts` such that the shop/host RegExp in sanitizeHost backtracks catastrophically on a redirect target that is protocol-relative (//evil.com), breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirect target that is protocol-relative (//evil.com)
- Exploit idea: the shop/host RegExp in sanitizeHost backtracks catastrophically on a redirect target that is protocol-relative (//evil.com)
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
