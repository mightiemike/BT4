# Q2771: auth/get-embedded-app-url — redirect protocol/relative

## Question
Can an unprivileged attacker submit a host that passes the '.myshopify.com$' suffix test via subdomain to `getEmbeddedAppUrl` in `auth/get-embedded-app-url.ts` such that sanitizeRedirectUrl/isSafe accepts a host that passes the '.myshopify.com$' suffix test via subdomain, breaking the invariant that only same-origin https redirects, and leading to: open redirect?

## Target
- File/function: `packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts` -> `getEmbeddedAppUrl`
- Entrypoint: Request with attacker-controlled shop/host/redirect params
- Attacker controls: a host that passes the '.myshopify.com$' suffix test via subdomain
- Exploit idea: sanitizeRedirectUrl/isSafe accepts a host that passes the '.myshopify.com$' suffix test via subdomain
- Invariant to test: only same-origin https redirects
- Expected Immunefi impact: Open redirect (In scope: open redirect leading to token theft, or SSRF. Note: shopify-app-js is covered under Shopify's HackerOne program, not Immunefi; SECURITY.md "Websites and Apps" exclusions apply.)
- Fast validation: protocol-relative test
