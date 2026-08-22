# Q0658: utils/shop-validator — open redirect via shop

## Question
Can an unprivileged attacker submit a shop string long enough to trigger regex backtracking to `sanitizeHost` in `utils/shop-validator.ts` such that sanitizeHost treats a shop string long enough to trigger regex backtracking as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop string long enough to trigger regex backtracking
- Exploit idea: sanitizeHost treats a shop string long enough to trigger regex backtracking as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a shop string long enough to trigger regex backtracking
