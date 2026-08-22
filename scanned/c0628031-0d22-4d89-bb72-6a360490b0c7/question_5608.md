# Q5608: helpers/validate-shop-and-host-params — embedded-url injection

## Question
Can an unprivileged attacker submit a shop param like 'evil.com?.myshopify.com' or with an embedded '@' to `validateShopAndHostParams` in `helpers/validate-shop-and-host-params.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a shop param like 'evil.com?.myshopify.com' or with an embedded '@' unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `validateShopAndHostParams`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop param like 'evil.com?.myshopify.com' or with an embedded '@'
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a shop param like 'evil.com?.myshopify.com' or with an embedded '@' unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
