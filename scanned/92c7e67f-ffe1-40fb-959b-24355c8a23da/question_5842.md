# Q5842: utils/domain-transformer — embedded-url injection

## Question
Can an unprivileged attacker submit a shop admin URL (admin.shopify.com/store/x) with crafted store handle to `getTransformationDomains` in `utils/domain-transformer.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a shop admin URL (admin.shopify.com/store/x) with crafted store handle unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/domain-transformer.ts` -> `getTransformationDomains`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop admin URL (admin.shopify.com/store/x) with crafted store handle
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a shop admin URL (admin.shopify.com/store/x) with crafted store handle unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
