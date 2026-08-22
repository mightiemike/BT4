# Q1971: utils/domain-transformer — decodeHost origin injection

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `applyDomainTransformations` in `utils/domain-transformer.ts` such that decodeHost yields an attacker origin from a host that passes the '.myshopify.com$' suffix test via subdomain, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `applyDomainTransformations`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: decodeHost yields an attacker origin from a host that passes the '.myshopify.com$' suffix test via subdomain
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
