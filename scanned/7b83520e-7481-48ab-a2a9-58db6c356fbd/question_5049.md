# Q5049: utils/domain-transformer — transformation-domains widening

## Question
Can an unprivileged attacker submit a redirectUrl using a protocol other than https to `applyDomainTransformations` in `utils/domain-transformer.ts` such that applyDomainTransformations lets a redirectUrl using a protocol other than https match via added domains, breaking the invariant that default config does not widen the allowlist, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `applyDomainTransformations`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl using a protocol other than https
- Exploit idea: applyDomainTransformations lets a redirectUrl using a protocol other than https match via added domains
- Invariant to test: default config does not widen the allowlist
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: default-config test
