# Q5053: helpers/validate-shop-and-host-params — transformation-domains widening

## Question
Can an unprivileged attacker submit a redirectUrl using a protocol other than https to `validateShopAndHostParams` in `helpers/validate-shop-and-host-params.ts` such that applyDomainTransformations lets a redirectUrl using a protocol other than https match via added domains, breaking the invariant that default config does not widen the allowlist, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `validateShopAndHostParams`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl using a protocol other than https
- Exploit idea: applyDomainTransformations lets a redirectUrl using a protocol other than https match via added domains
- Invariant to test: default config does not widen the allowlist
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: default-config test
