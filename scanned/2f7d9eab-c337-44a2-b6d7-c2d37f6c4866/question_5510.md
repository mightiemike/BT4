# Q5510: helpers/get-shop-from-request — transformation-domains widening

## Question
Can an unprivileged attacker submit a shop param with an embedded null byte before .myshopify.com to `getShopFromRequest` in `helpers/get-shop-from-request.ts` such that applyDomainTransformations lets a shop param with an embedded null byte before .myshopify.com match via added domains, breaking the invariant that default config does not widen the allowlist, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts` -> `getShopFromRequest`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param with an embedded null byte before .myshopify.com
- Exploit idea: applyDomainTransformations lets a shop param with an embedded null byte before .myshopify.com match via added domains
- Invariant to test: default config does not widen the allowlist
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: default-config test
