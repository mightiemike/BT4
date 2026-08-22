# Q5811: helpers/validate-redirect-url — embedded-url injection

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `sanitizeRedirectUrl` in `helpers/validate-redirect-url.ts` such that buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a host that passes the '.myshopify.com$' suffix test via subdomain unsanitized, breaking the invariant that embedded app URL host is verified, and leading to: xss/redirect in embedded frame?

## Target
- File/function: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` -> `sanitizeRedirectUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: buildEmbeddedAppUrl/getEmbeddedAppUrl embeds a host that passes the '.myshopify.com$' suffix test via subdomain unsanitized
- Invariant to test: embedded app URL host is verified
- Expected Immunefi impact: XSS/redirect in embedded frame (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: host-injection test
