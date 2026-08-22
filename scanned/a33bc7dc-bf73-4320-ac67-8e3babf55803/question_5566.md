# Q5566: helpers/validate-shop-and-host-params — transformation-domains widening

## Question
Can an unprivileged attacker submit a host that decodes to a userinfo@ origin to `redirectToLoginPath` in `helpers/validate-shop-and-host-params.ts` such that applyDomainTransformations lets a host that decodes to a userinfo@ origin match via added domains, breaking the invariant that default config does not widen the allowlist, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `redirectToLoginPath`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that decodes to a userinfo@ origin
- Exploit idea: applyDomainTransformations lets a host that decodes to a userinfo@ origin match via added domains
- Invariant to test: default config does not widen the allowlist
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: default-config test
