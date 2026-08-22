# Q5396: helpers/get-shop-from-request — transformation-domains widening

## Question
Can an unprivileged attacker submit a host param with CRLF to split headers on redirect to `getShopFromRequest` in `helpers/get-shop-from-request.ts` such that applyDomainTransformations lets a host param with CRLF to split headers on redirect match via added domains, breaking the invariant that default config does not widen the allowlist, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/get-shop-from-request.ts` -> `getShopFromRequest`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host param with CRLF to split headers on redirect
- Exploit idea: applyDomainTransformations lets a host param with CRLF to split headers on redirect match via added domains
- Invariant to test: default config does not widen the allowlist
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: default-config test
