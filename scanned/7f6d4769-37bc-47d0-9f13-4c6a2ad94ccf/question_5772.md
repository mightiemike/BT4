# Q5772: utils/shop-validator — embedded-url injection

## Question
Can an unprivileged attacker submit a shop with a trailing dot, path, or extra label to `sanitizeHost` in `utils/shop-validator.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a shop with a trailing dot, path, or extra label unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-api/lib/utils/shop-validator.ts` -> `sanitizeHost`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a shop with a trailing dot, path, or extra label
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a shop with a trailing dot, path, or extra label unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
