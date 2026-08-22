# Q3225: utils/domain-transformer — regex backtracking DoS

## Question
Can an unprivileged attacker submit a shop param like 'evil.com?.myshopify.com' or with an embedded '@' to `applyDomainTransformations` in `utils/domain-transformer.ts` such that the shop/host RegExp in applyDomainTransformations backtracks catastrophically on a shop param like 'evil.com?.myshopify.com' or with an embedded '@', breaking the invariant that validation runs in linear time, and leading to: dos of auth endpoint?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `applyDomainTransformations`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Exploit idea: the shop/host RegExp in applyDomainTransformations backtracks catastrophically on a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Invariant to test: validation runs in linear time
- Expected Immunefi impact: DoS of auth endpoint (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: long-input timing test
