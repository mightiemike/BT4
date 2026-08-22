# Q1342: utils/shop-validator — host allowlist bypass

## Question
Can an unprivileged attacker submit a redirect target that is protocol-relative (//evil.com) to `sanitizeHost` in `utils/shop-validator.ts` such that sanitizeHost passes a redirect target that is protocol-relative (//evil.com) through the domain suffix check, breaking the invariant that host validated against exact allowed origins, and leading to: open redirect / embedded-url injection?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirect target that is protocol-relative (//evil.com)
- Exploit idea: sanitizeHost passes a redirect target that is protocol-relative (//evil.com) through the domain suffix check
- Invariant to test: host validated against exact allowed origins
- Expected Immunefi impact: Open redirect / embedded-URL injection (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: suffix-bypass test
