# Q0261: utils/domain-transformer — open redirect via shop

## Question
Can an unprivileged attacker submit a redirectUrl using a protocol other than https to `applyDomainTransformations` in `utils/domain-transformer.ts` such that applyDomainTransformations treats a redirectUrl using a protocol other than https as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `applyDomainTransformations`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl using a protocol other than https
- Exploit idea: applyDomainTransformations treats a redirectUrl using a protocol other than https as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a redirectUrl using a protocol other than https
