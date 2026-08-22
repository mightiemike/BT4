# Q1918: helpers/validate-shop-and-host-params — decodeHost origin injection

## Question
Can an unprivileged attacker submit a shop with a trailing dot, path, or extra label to `redirectToLoginPath` in `helpers/validate-shop-and-host-params.ts` such that decodeHost yields an attacker origin from a shop with a trailing dot, path, or extra label, breaking the invariant that decoded host constrained to Shopify origins, and leading to: ssrf / redirect to attacker?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `redirectToLoginPath`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop with a trailing dot, path, or extra label
- Exploit idea: decodeHost yields an attacker origin from a shop with a trailing dot, path, or extra label
- Invariant to test: decoded host constrained to Shopify origins
- Expected Immunefi impact: SSRF / redirect to attacker (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: base64 host test
