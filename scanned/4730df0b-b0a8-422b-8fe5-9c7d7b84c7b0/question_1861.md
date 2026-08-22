# Q1861: helpers/validate-shop-and-host-params — decodeHost origin injection

## Question
Can an unprivileged attacker submit a redirectUrl using a protocol other than https to `validateShopAndHostParams` in `helpers/validate-shop-and-host-params.ts` such that decodeHost yields an attacker origin from a redirectUrl using a protocol other than https, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `validateShopAndHostParams`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl using a protocol other than https
- Exploit idea: decodeHost yields an attacker origin from a redirectUrl using a protocol other than https
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
