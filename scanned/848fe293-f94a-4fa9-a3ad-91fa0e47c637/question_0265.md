# Q0265: helpers/validate-shop-and-host-params — open redirect via shop

## Question
Can an unprivileged attacker submit a redirectUrl using a protocol other than https to `validateShopAndHostParams` in `helpers/validate-shop-and-host-params.ts` such that validateShopAndHostParams treats a redirectUrl using a protocol other than https as a valid shop and redirects there, breaking the invariant that only allow-listed *.myshopify.com destinations, and leading to: open redirect -> session-token phishing?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `validateShopAndHostParams`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl using a protocol other than https
- Exploit idea: validateShopAndHostParams treats a redirectUrl using a protocol other than https as a valid shop and redirects there
- Invariant to test: only allow-listed *.myshopify.com destinations
- Expected Immunefi impact: Open redirect -> session-token phishing (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: regex bypass test with a redirectUrl using a protocol other than https
