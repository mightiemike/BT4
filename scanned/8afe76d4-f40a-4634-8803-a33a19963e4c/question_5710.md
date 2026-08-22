# Q5710: helpers/validate-shop-and-host-params — embedded-url injection

## Question
Can an unprivileged attacker submit a redirectUrl with '\\/\\/evil.com' or backslash tricks to `redirectToLoginPath` in `helpers/validate-shop-and-host-params.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a redirectUrl with '\\/\\/evil.com' or backslash tricks unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts` -> `redirectToLoginPath`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a redirectUrl with '\\/\\/evil.com' or backslash tricks
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a redirectUrl with '\\/\\/evil.com' or backslash tricks unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
