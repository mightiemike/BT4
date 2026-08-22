# Q1173: utils/domain-transformer — host allowlist bypass

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `applyDomainTransformations` in `utils/domain-transformer.ts` such that applyDomainTransformations passes a host that passes the '.myshopify.com$' suffix test via subdomain through the domain suffix check, breaking the invariant that host validated against exact allowed origins, and leading to: open redirect / embedded-url injection?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `applyDomainTransformations`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: applyDomainTransformations passes a host that passes the '.myshopify.com$' suffix test via subdomain through the domain suffix check
- Invariant to test: host validated against exact allowed origins
- Expected Immunefi impact: Open redirect / embedded-URL injection (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: suffix-bypass test
