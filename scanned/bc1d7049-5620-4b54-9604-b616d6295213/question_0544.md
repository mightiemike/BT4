# Q0544: utils/shop-validator — open redirect via shop

## Question
Can an unprivileged attacker submit a redirect target that is protocol-relative (//evil.com) to `sanitizeHost` in `utils/shop-validator.ts` such that sanitizeHost treats a redirect target that is protocol-relative (//evil.com) as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirect target that is protocol-relative (//evil.com)
- Exploit idea: sanitizeHost treats a redirect target that is protocol-relative (//evil.com) as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a redirect target that is protocol-relative (//evil.com)
