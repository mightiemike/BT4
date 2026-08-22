# Q5109: helpers/validate-redirect-url — transformation-domains widening

## Question
Can an unprivileged attacker submit a shop with a trailing dot, path, or extra label to `isSafe` in `helpers/validate-redirect-url.ts` such that applyDomainTransformations lets a shop with a trailing dot, path, or extra label match via added domains, breaking the invariant that default config does not widen the allowlist, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `isSafe`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop with a trailing dot, path, or extra label
- Exploit idea: applyDomainTransformations lets a shop with a trailing dot, path, or extra label match via added domains
- Invariant to test: default config does not widen the allowlist
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: default-config test
